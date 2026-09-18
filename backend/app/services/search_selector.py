import re
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional, List, Dict
import logging
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel

from app.domain.taxonomy import get_authoritative_taxonomy
from app.domain.candidate import SearchCandidate
from app.db.models.search_execution import SearchExecutionModel

logger = logging.getLogger(__name__)

ABANDONED_CLAIM_MINUTES = 15
SUCCESS_COOLDOWN_HOURS = 24

class CycleBudgetExhausted(Exception):
    pass

class SelectionResult(BaseModel):
    action: str = 'execute'
    execution_id: Optional[int] = None
    candidate: Optional[SearchCandidate]
    reason: str
    score: Optional[int] = None
    policy_version: str = "v1"

# Compile regexes once at module level for performance
FRESHER_PATTERN = re.compile(r'\b(fresher|junior|jr\.?|entry[- ]level|graduate|trainee)\b', re.IGNORECASE)
NORMALIZE_SPACES_PATTERN = re.compile(r'[\s\-]+')

def _build_bounded_variants(canonical: str, aliases: List[str]) -> List[str]:
    seen_normalized = set()
    fresher_aliases = []

    for alias in aliases:
        if FRESHER_PATTERN.search(alias):
            # Normalize: lower case, hyphens to spaces, collapse spaces
            norm = NORMALIZE_SPACES_PATTERN.sub(' ', alias).strip().lower()
            if norm not in seen_normalized:
                seen_normalized.add(norm)
                fresher_aliases.append(alias)

    if fresher_aliases:
        def sort_key(a):
            norm = NORMALIZE_SPACES_PATTERN.sub(' ', a).strip().lower()
            return (len(norm), norm)

        fresher_aliases.sort(key=sort_key)
        best_fresher = fresher_aliases[0]

        canon_norm = NORMALIZE_SPACES_PATTERN.sub(' ', canonical).strip().lower()
        best_norm = NORMALIZE_SPACES_PATTERN.sub(' ', best_fresher).strip().lower()

        if canon_norm == best_norm:
            return [canonical]
        return [best_fresher, canonical]

    return [canonical]

def generate_candidates(db: Optional[Session] = None) -> List[SearchCandidate]:

    """Generates the bounded set of all theoretical search candidates from taxonomy and user searches."""
    taxonomy = get_authoritative_taxonomy()
    candidates = []

    for role in taxonomy.roles:
        for location in taxonomy.locations:
            # Deterministic candidate ID
            cid = f"{role.id}::{location.id}"

            candidates.append(SearchCandidate(
                candidate_id=cid,
                role_id=role.id,
                location_id=location.id,
                role_canonical=role.canonical,
                location_canonical=location.canonical,
                priority=min(role.priority, location.priority), # Stricter/lower number is better
                tier=location.tier,
                variants=_build_bounded_variants(role.canonical, role.aliases)
            ))

    # Ad-hoc user queries are no longer injected into the autonomous taxonomy loop

    # Sort deterministically
    candidates.sort(key=lambda c: (c.priority, c.tier, c.candidate_id))
    return candidates

def resolve_candidate(db: Session, candidate_id: str) -> Optional[SearchCandidate]:
    """Resolves an exact candidate for execution, independent of the bounded scheduler window."""
    if candidate_id.startswith("user_search::"):
        parts = candidate_id.split("::")
        if len(parts) != 2:
            return None
        try:
            us_id = int(parts[1])
        except ValueError:
            return None
        from app.db.models.user_search import UserSearch
        us = db.query(UserSearch).filter(UserSearch.id == us_id, UserSearch.enabled == True).first()
        if not us:
            return None
        return SearchCandidate(
            candidate_id=candidate_id,
            role_id="user_search",
            location_id="user_search",
            role_canonical=us.query or "",
            location_canonical=us.location or "",
            priority=3,
            tier=2
        )
    else:
        taxonomy = get_authoritative_taxonomy()
        parts = candidate_id.split("::")
        if len(parts) != 2:
            return None
        rid, lid = parts
        role = next((r for r in taxonomy.roles if r.id == rid), None)
        location = next((l for l in taxonomy.locations if l.id == lid), None)
        if not role or not location:
            return None
        return SearchCandidate(
            candidate_id=candidate_id,
            role_id=role.id,
            location_id=location.id,
            role_canonical=role.canonical,
            location_canonical=location.canonical,
            priority=min(role.priority, location.priority),
            tier=location.tier,
            variants=_build_bounded_variants(role.canonical, role.aliases)
        )


def _get_history(db: Session, candidate_ids: List[str]) -> Dict[str, dict]:
    """Fetches freshness and success history for the given candidates."""
    history = {}
    if not candidate_ids:
        return history

    stmt = text("""
        SELECT candidate_id,
               MAX(CASE WHEN status = 'succeeded' THEN completed_at ELSE NULL END) as last_success,
               MAX(selected_at) as last_selected,
               COUNT(CASE WHEN status = 'succeeded' THEN 1 END) as success_count
        FROM search_execution
        WHERE candidate_id = ANY(:cids)
        GROUP BY candidate_id
    """)
    rows = db.execute(stmt, {"cids": candidate_ids}).fetchall()

    for row in rows:
        history[row[0]] = {
            "last_success": row[1],
            "last_selected": row[2],
            "success_count": row[3] if len(row) > 3 else 0
        }
    return history


def _get_variant_history(db: Session, candidate_ids: List[str]) -> Dict[str, Dict[str, dict]]:
    if not candidate_ids:
        return {}

    stmt = text("""
        SELECT DISTINCT ON (candidate_id, query_variant)
               candidate_id, query_variant, jobs_fetched, jobs_fresher_eligible, completed_at
        FROM search_execution
        WHERE candidate_id = ANY(:cids)
          AND status = 'succeeded'
          AND query_variant IS NOT NULL
        ORDER BY candidate_id, query_variant, completed_at DESC
    """)
    rows = db.execute(stmt, {"cids": candidate_ids}).fetchall()

    vh = {cid: {} for cid in candidate_ids}
    for row in rows:
        cid, variant, fetched, eligible, comp_at = row
        vh[cid][variant] = {
            "fetched": fetched or 0,
            "eligible": eligible if eligible is not None else -1,
            "completed_at": comp_at
        }
    return vh

def _clean_abandoned_claims(db: Session, reference_time: datetime | None = None) -> int:
    """Fails claims that were selected more than 15 minutes ago but never transitioned to started.

    A catastrophic failure after selected -> started may leave the execution in started.
    The current abandonment cleanup only reclaims stale selected claims. Recovery/reclamation
    of stale started executions is deferred to a future milestone.
    """
    threshold = (reference_time or datetime.now(timezone.utc)) - timedelta(
        minutes=ABANDONED_CLAIM_MINUTES
    )
    stmt = text("""
        UPDATE search_execution
        SET status = 'failed', completed_at = :now, error_message = 'abandoned claim'
        WHERE status = 'selected' AND selected_at < :threshold
    """)
    try:
        with Session(db.get_bind()) as cleanup_db:
            result = cleanup_db.execute(
                stmt,
                {"now": reference_time or datetime.now(timezone.utc), "threshold": threshold},
            )
            if result.rowcount > 0:
                cleanup_db.commit()
            return result.rowcount
    except Exception as e:
        logger.exception("Failed to clean abandoned claims")
        return 0


def select_next_search(
    db: Session,
    reference_time: datetime | None = None,
    before_claim: Callable[[SearchCandidate], None] | None = None,
    cycle_id: str | None = None,
) -> SelectionResult:
    """Deterministically selects exactly ONE search candidate.
    If cycle_id is None, it falls back to legacy single-search behavior without budget limits.
    """
    now = reference_time or datetime.now(timezone.utc)

    # 1. Clean abandoned claims so they don't permanently poison candidates
    _clean_abandoned_claims(db, now)

    # 2. Generate bounds
    candidates = generate_candidates(db)
    cids = [c.candidate_id for c in candidates]

    # 3. Fetch history
    history = _get_history(db, cids)

    cooldown_success = timedelta(hours=SUCCESS_COOLDOWN_HOURS)
    cooldown_selected = timedelta(minutes=ABANDONED_CLAIM_MINUTES)
    penalty_duration = timedelta(days=7)

    # 4. Evaluate eligibility
    pre_eligible = []
    for c in candidates:
        h = history.get(c.candidate_id, {})
        last_success = h.get("last_success")
        last_selected = h.get("last_selected")

        if last_selected and (now - last_selected) < cooldown_selected:
            continue
        if last_success and (now - last_success) < cooldown_success:
            continue

        pre_eligible.append((c, h))

    # Fetch variant histories just for pre_eligible
    cids_eligible = [c.candidate_id for c, _ in pre_eligible]
    variant_histories = _get_variant_history(db, cids_eligible)

    eligible = []
    for c, h in pre_eligible:
        score = (c.priority * 100) + (c.tier * 10)
        last_selected = h.get("last_selected")
        last_success = h.get("last_success")

        if last_selected is None:
            score -= 1000
        elif last_success is None:
            score -= 500

        success_count = h.get("success_count", 0)

        if c.variants:
            v_hist = variant_histories.get(c.candidate_id, {})
            available = []

            for v in c.variants:
                vh = v_hist.get(v)
                if not vh:
                    available.append(v)
                    continue

                # Check WEAK/NO-YIELD condition
                is_weak_or_no_yield = (vh["eligible"] == 0)
                is_recent = (now - vh["completed_at"]) < penalty_duration

                if is_weak_or_no_yield and is_recent:
                    continue # Penalized
                available.append(v)

            if not available:
                available = c.variants # Fallback: all penalized

            c.query_variant = available[success_count % len(available)]
        else:
            c.query_variant = c.role_canonical

        eligible.append((score, c))

    if not eligible:
        return SelectionResult(action="stop", candidate=None, reason="all_candidates_ineligible_or_fresh")

    # Sort by score ascending, then by deterministic candidate ID
    eligible.sort(key=lambda x: (x[0], x[1].candidate_id))

    # 5. Concurrency-safe claim mechanism
    from app.core.config import settings

    for score, candidate in eligible:
        if before_claim:
            before_claim(candidate)
        try:
            with db.begin_nested():
                # Atomic Budget Reservation
                if cycle_id is not None:
                    stmt = text("""
                        INSERT INTO search_cycle_usage (cycle_id, execution_count)
                        VALUES (:id, 1)
                        ON CONFLICT (cycle_id)
                        DO UPDATE SET execution_count = search_cycle_usage.execution_count + 1
                        RETURNING execution_count
                    """)
                    count = db.execute(stmt, {"id": cycle_id}).scalar_one()
                    if count > settings.cycle_budget:
                        raise CycleBudgetExhausted()

                # Create Execution Claim
                claim = SearchExecutionModel(
                    candidate_id=candidate.candidate_id,
                    status='selected',
                    selected_at=now,
                    cycle_id=cycle_id,
                    query_variant=candidate.query_variant
                )
                db.add(claim)
                db.flush() # Force IntegrityError if concurrent insert
            # Savepoint committed successfully
            return SelectionResult(action="execute", candidate=candidate, reason="highest_ranked_eligible", score=score, execution_id=claim.id)

        except CycleBudgetExhausted:
            # Savepoint automatically rolled back. Budget hit.
            return SelectionResult(action="stop", candidate=None, reason="cycle_budget_exhausted")
        except IntegrityError as exc:
            # Savepoint automatically rolled back. Candidate claimed by another worker.
            if getattr(exc.orig, "sqlstate", None) != "23505":
                raise
            continue
        except Exception:
            logger.exception(
                "Failed to claim search candidate candidate_id=%s",
                candidate.candidate_id,
            )
            raise

    return SelectionResult(action="stop", candidate=None, reason="all_eligible_candidates_claimed_by_others")
