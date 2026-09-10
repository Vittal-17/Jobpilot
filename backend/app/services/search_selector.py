from datetime import datetime, timezone, timedelta
from typing import Callable, Optional, List, Dict
import logging
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel

from app.domain.taxonomy import get_authoritative_taxonomy
from app.domain.candidate import SearchCandidate
from app.services.quota_policy import get_provider_policy
from app.db.models.search_execution import SearchExecutionModel

logger = logging.getLogger(__name__)

ABANDONED_CLAIM_MINUTES = 15
SUCCESS_COOLDOWN_HOURS = 24

class SelectionResult(BaseModel):
    execution_id: Optional[int] = None
    candidate: Optional[SearchCandidate]
    reason: str
    score: Optional[int] = None
    policy_version: str = "v1"

def generate_candidates() -> List[SearchCandidate]:
    """Generates the bounded set of all theoretical search candidates from taxonomy."""
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
                tier=location.tier
            ))

    # Sort deterministically
    candidates.sort(key=lambda c: (c.priority, c.tier, c.candidate_id))
    return candidates


def get_provider_remaining_capacity(
    db: Session,
    provider_name: str,
    reference_time: datetime | None = None,
) -> bool:
    policy = get_provider_policy(provider_name)

    minute_limit = policy.get_effective_limit('minute')
    daily_limit = policy.get_effective_limit('daily')
    lifetime_limit = policy.get_effective_limit('lifetime')

    if (minute_limit is not None and minute_limit <= 0) or \
       (daily_limit is not None and daily_limit <= 0) or \
       (lifetime_limit is not None and lifetime_limit <= 0):
        return False

    now_utc = reference_time or datetime.now(timezone.utc)
    current_minute = now_utc.replace(second=0, microsecond=0)
    today = now_utc.date()

    # Minute
    if minute_limit is not None:
        stmt = text("SELECT request_count FROM provider_minute_usage WHERE provider_name = :p AND usage_minute = :m")
        usage = db.execute(stmt, {"p": provider_name, "m": current_minute}).scalar() or 0
        if minute_limit - usage <= 0:
            return False

    # Daily
    if daily_limit is not None:
        stmt = text("SELECT request_count FROM provider_usage WHERE provider_name = :p AND usage_date = :d")
        usage = db.execute(stmt, {"p": provider_name, "d": today}).scalar() or 0
        if daily_limit - usage <= 0:
            return False

    # Lifetime
    if lifetime_limit is not None:
        stmt = text("SELECT lifetime_count FROM provider_state WHERE provider_name = :p")
        usage = db.execute(stmt, {"p": provider_name}).scalar() or 0
        if lifetime_limit - usage <= 0:
            return False

    return True

def _get_history(db: Session, candidate_ids: List[str]) -> Dict[str, dict]:
    """Fetches freshness and success history for the given candidates."""
    history = {}
    if not candidate_ids:
        return history

    stmt = text("""
        SELECT candidate_id,
               MAX(CASE WHEN status = 'succeeded' THEN completed_at ELSE NULL END) as last_success,
               MAX(selected_at) as last_selected
        FROM search_execution
        WHERE candidate_id = ANY(:cids)
        GROUP BY candidate_id
    """)
    rows = db.execute(stmt, {"cids": candidate_ids}).fetchall()

    for row in rows:
        history[row[0]] = {
            "last_success": row[1],
            "last_selected": row[2]
        }
    return history

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
        with db.begin_nested():
            result = db.execute(
                stmt,
                {"now": reference_time or datetime.now(timezone.utc), "threshold": threshold},
            )
        # explicitly do NOT outer-commit here. Let the caller (or the later select commit) handle it,
        # or we just rely on nested transaction. Actually, the outer transaction must commit this.
        # But wait, if select_next_search fails to find a claim, it does not commit!
        # So we SHOULD commit the cleanup if we want it to persist even when no claims are made.
        # However, to avoid committing caller state, we should only commit if we are the transaction owner,
        # or use a separate DB session. Since we are passed `db`, we should assume the caller commits
        # OR we just do db.commit() because the API route `Depends(get_db)` expects us to manage our own writes.
        # Let's commit it but wrap it so we only commit if there were changes.
        if result.rowcount > 0:
            db.commit()
        return result.rowcount
    except Exception as e:
        logger.exception("Failed to clean abandoned claims")
        return 0


def select_next_search(
    db: Session,
    reference_time: datetime | None = None,
    before_claim: Callable[[SearchCandidate], None] | None = None,
) -> SelectionResult:
    """Deterministically selects exactly ONE search candidate."""
    now = reference_time or datetime.now(timezone.utc)

    # 1. Clean abandoned claims so they don't permanently poison candidates
    _clean_abandoned_claims(db, now)

    # 2. Check Quota (If no quota for Adzuna, we can't search)
    if not get_provider_remaining_capacity(db, "adzuna", now):
        return SelectionResult(candidate=None, reason="quota_exhausted")

    # 3. Generate bounds
    candidates = generate_candidates()
    cids = [c.candidate_id for c in candidates]

    # 4. Fetch history
    history = _get_history(db, cids)

    cooldown_success = timedelta(hours=SUCCESS_COOLDOWN_HOURS)
    cooldown_selected = timedelta(minutes=ABANDONED_CLAIM_MINUTES)

    # 5. Evaluate eligibility and rank
    eligible = []
    for c in candidates:
        h = history.get(c.candidate_id, {})
        last_success = h.get("last_success")
        last_selected = h.get("last_selected")

        # Freshness Check
        if last_selected and (now - last_selected) < cooldown_selected:
            continue # Claimed/in-flight
        if last_success and (now - last_success) < cooldown_success:
            continue # Recently searched

        # Score calculation (lower is better for ranking)
        # Priority (1-3) is heavily weighted.
        # Tier (0-2) is secondary.
        score = (c.priority * 100) + (c.tier * 10)

        if last_selected is None:
            # A. Never attempted
            score -= 1000
        elif last_success is None:
            # B. Previously failed (attempted, but no success yet)
            score -= 500
        # C. Previously succeeded (but off cooldown) gets no bonus.

        eligible.append((score, c))

    if not eligible:
        return SelectionResult(candidate=None, reason="all_candidates_ineligible_or_fresh")

    # Sort by score ascending, then by deterministic candidate ID
    eligible.sort(key=lambda x: (x[0], x[1].candidate_id))

    # 6. Concurrency-safe claim mechanism
    for score, candidate in eligible:
        if before_claim:
            before_claim(candidate)
        try:
            # Create a savepoint
            with db.begin_nested():
                claim = SearchExecutionModel(
                    candidate_id=candidate.candidate_id,
                    status='selected',
                    selected_at=now
                )
                db.add(claim)
            db.commit()
            return SelectionResult(candidate=candidate, reason="highest_ranked_eligible", score=score, execution_id=claim.id)
        except IntegrityError as exc:
            # Partial unique constraint 'uq_active_claim' blocked us.
            # Another worker claimed it. Try the next best candidate.
            db.rollback()
            if getattr(exc.orig, "sqlstate", None) != "23505":
                raise
        except Exception:
            db.rollback()
            logger.exception(
                "Failed to claim search candidate candidate_id=%s",
                candidate.candidate_id,
            )
            raise

    return SelectionResult(candidate=None, reason="all_eligible_candidates_claimed_by_others")
