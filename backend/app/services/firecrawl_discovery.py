import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.user_search import UserSearch
from app.db.models.user_profile import UserProfile
from app.db.models.search_execution import SearchExecutionModel
from app.db.models.execution_outcome import FirecrawlDiscoveryRunModel, FirecrawlQueryStateModel
from app.db.models.execution_outcome import ProviderReservationModel
from app.domain.taxonomy import get_authoritative_taxonomy
from app.providers.registry import create_provider
from app.providers.types import ProviderName
from app.providers.exceptions import ProviderError, ProviderConfigurationError
from app.schemas.job_search import JobSearchQuery, IngestionResult
from app.services.firecrawl_quota import (
    calculate_search_credits,
    DISCOVERY_LEASE_SECONDS,
    DiscoveryOwnershipLost,
    get_firecrawl_budget_state,
    record_firecrawl_operation,
    reconcile_discovery_reservations,
    reconcile_stale_discovery_reservations,
    renew_discovery_ownership,
    assert_discovery_ownership,
)
from app.services.ingestion import (
    run_ingestion,
    RateLimitExceeded,
    DatabaseUnavailable,
    PaymentRequired,
    ProviderSettlementError,
)
from app.services.firecrawl_result_staging import recover_staged_firecrawl_result

logger = logging.getLogger(__name__)

FRESHER_INDICATORS = ("fresher", "freshers", "entry level", "junior", "jr", "graduate", "trainee", "intern")


class DiscoveryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keywords: str = Field(..., min_length=1, max_length=100)
    location: str = Field(..., min_length=1, max_length=100)
    page_size: int = Field(10, ge=1, le=100)
    estimated_credits: int = Field(2, ge=1)


class IngestionStats(BaseModel):
    created: int = 0
    duplicates: int = 0
    invalid: int = 0
    failed: int = 0


class FirecrawlDiscoveryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    max_queries: int = 2
    max_results_per_query: int = 10
    max_credits_per_run: int = 20
    queries: list[DiscoveryQuery] = Field(default_factory=list)
    reason: str | None = None
    # n8n supplies this from $execution.id.  It is deliberately not part of
    # the plan hash (the same plan may be submitted by a wrapper or directly).
    run_id: str | None = Field(None, max_length=64, min_length=1, pattern=r"^\S(.*\S)?$")


class FirecrawlDiscoveryTelemetry(BaseModel):
    run_id: str | None = None
    status: str  # "completed", "quota_stopped", "disabled", "failed"
    query_count: int = 0
    attempted_queries: int = 0
    successful_queries: int = 0
    candidates_found: int = 0
    candidates_accepted: int = 0
    candidates_rejected: int = 0
    validated_individual: int = 0
    rejections_by_reason: dict[str, int] = Field(default_factory=dict)
    ingestion: IngestionStats = Field(default_factory=IngestionStats)
    credits_consumed: int = 0  # Authoritative credits reserved in provider_usage
    credits_reserved: int = 0  # Explicit alias for internal quota reservation
    estimated_credits: int = 0  # Authoritative planned credit cost
    reason: str | None = None
    execution_ids: list[int] = Field(default_factory=list)


def _format_discovery_keywords(raw_role: str) -> str:
    clean = raw_role.strip()
    lower = clean.lower()
    if not any(tok in lower for tok in FRESHER_INDICATORS):
        return f"{clean} fresher"
    return clean


def plan_discovery_queries(db: Session, force: bool = False, run_id: str | None = None) -> FirecrawlDiscoveryPlan:
    enabled = bool(getattr(settings, "firecrawl_discovery_enabled", False)) or force
    max_queries = getattr(settings, "firecrawl_discovery_max_queries_per_run", 2)
    max_results = getattr(settings, "firecrawl_discovery_max_results_per_query", 10)
    max_credits = getattr(settings, "firecrawl_discovery_max_credits_per_run", 20)

    if not enabled:
        return FirecrawlDiscoveryPlan(
            enabled=False,
            max_queries=max_queries,
            max_results_per_query=max_results,
            max_credits_per_run=max_credits,
            queries=[],
            reason="Firecrawl discovery is disabled",
            run_id=run_id,
        )

    budget_state = get_firecrawl_budget_state(db)
    if budget_state["is_exhausted"] or budget_state["firecrawl_monthly_remaining"] < 2:
        return FirecrawlDiscoveryPlan(
            enabled=True,
            max_queries=max_queries,
            max_results_per_query=max_results,
            max_credits_per_run=max_credits,
            queries=[],
            reason="Firecrawl monthly automation budget exhausted",
            run_id=run_id,
        )

    candidates: list[tuple[str, str]] = []

    # 1. Active User Searches
    user_searches = db.query(UserSearch).filter(UserSearch.enabled == True).all()
    for us in user_searches:
        kw = (us.query or "").strip()
        loc = (us.location or "").strip() or "Bengaluru"
        if kw:
            candidates.append((_format_discovery_keywords(kw), loc))

    # 2. User Profile preferences
    if len(candidates) < max_queries:
        profiles = db.query(UserProfile).all()
        for p in profiles:
            if p.preferred_roles:
                roles = [r.strip() for r in p.preferred_roles.split(",") if r.strip()]
                loc = (p.preferred_locations or "Bengaluru").split(",")[0].strip() or "Bengaluru"
                for r in roles:
                    candidates.append((_format_discovery_keywords(r), loc))

    # 3. Authoritative Taxonomy fallback
    if len(candidates) < max_queries:
        taxonomy = get_authoritative_taxonomy()
        default_loc = taxonomy.locations[0].canonical if taxonomy.locations else "Bengaluru"
        for role in taxonomy.roles:
            candidates.append((_format_discovery_keywords(role.canonical), default_loc))

    # Deduplicate and apply per-run limits
    seen = set()
    bounded_queries: list[DiscoveryQuery] = []
    total_estimated_credits = 0

    credits_per_query = calculate_search_credits(max_results)

    for kw, loc in candidates:
        key = (kw.lower(), loc.lower())
        if key in seen:
            continue
        seen.add(key)

        if len(bounded_queries) >= max_queries:
            break
        if total_estimated_credits + credits_per_query > max_credits:
            break

        bounded_queries.append(
            DiscoveryQuery(
                keywords=kw,
                location=loc,
                page_size=max_results,
                estimated_credits=credits_per_query,
            )
        )
        total_estimated_credits += credits_per_query

    return FirecrawlDiscoveryPlan(
        enabled=True,
        max_queries=max_queries,
        max_results_per_query=max_results,
        max_credits_per_run=max_credits,
        queries=bounded_queries,
        reason="Plan generated successfully",
        run_id=run_id,
    )


class DiscoveryPlanConflict(ValueError):
    """The stable run id was previously claimed for a different plan."""


DISCOVERY_RUN_STALE_SECONDS = DISCOVERY_LEASE_SECONDS


def _canonical_query_key(keywords: str, location: str, page_size: int) -> str:
    payload = json.dumps(
        {"keywords": keywords.strip(), "location": location.strip(), "page_size": int(page_size)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _authoritative_plan_payload(plan: FirecrawlDiscoveryPlan) -> dict[str, Any]:
    """Return the bounded plan representation used for idempotency."""
    max_queries = int(getattr(settings, "firecrawl_discovery_max_queries_per_run", 2))
    max_results = int(getattr(settings, "firecrawl_discovery_max_results_per_query", 10))
    max_credits = int(getattr(settings, "firecrawl_discovery_max_credits_per_run", 20))
    queries: list[dict[str, Any]] = []
    total = 0
    for query in plan.queries[:max_queries]:
        page_size = min(max(1, int(query.page_size)), max_results)
        cost = calculate_search_credits(page_size)
        if total + cost > max_credits:
            break
        queries.append({
            "keywords": query.keywords.strip(),
            "location": query.location.strip(),
            "page_size": page_size,
            "estimated_credits": cost,
        })
        total += cost
    return {
        "enabled": bool(plan.enabled),
        "max_queries": max_queries,
        "max_results_per_query": max_results,
        "max_credits_per_run": max_credits,
        "queries": queries,
    }


def _plan_hash(plan: FirecrawlDiscoveryPlan) -> str:
    canonical = json.dumps(_authoritative_plan_payload(plan), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _telemetry_from_receipt(run_id: str, receipt: Any) -> FirecrawlDiscoveryTelemetry | None:
    if not isinstance(receipt, dict):
        return None
    try:
        return FirecrawlDiscoveryTelemetry.model_validate({**receipt, "run_id": run_id})
    except Exception:
        logger.warning("Ignoring malformed Firecrawl discovery receipt run_id=%s", run_id)
        return None


def _claim_discovery_run(db: Session, run_id: str, plan_hash: str) -> tuple[str, FirecrawlDiscoveryRunModel | None]:
    """Atomically claim a stable run id before any paid provider request."""
    owner_token = uuid.uuid4().hex
    owner_expires_at = datetime.now(timezone.utc) + timedelta(seconds=DISCOVERY_LEASE_SECONDS)
    stmt = insert(FirecrawlDiscoveryRunModel).values(
        run_id=run_id,
        plan_hash=plan_hash,
        status="running",
        receipt=None,
        owner_token=owner_token,
        owner_expires_at=owner_expires_at,
        started_at=datetime.now(timezone.utc),
    ).on_conflict_do_nothing(index_elements=[FirecrawlDiscoveryRunModel.run_id]).returning(
        FirecrawlDiscoveryRunModel.run_id
    )
    inserted = db.execute(stmt).scalar_one_or_none()
    if inserted is not None:
        # Make the ownership fence visible to concurrent workers while this
        # worker performs the external request.  A second worker observes the
        # running row and never calls Firecrawl.
        db.commit()
        return "claimed", db.get(FirecrawlDiscoveryRunModel, run_id)

    row = db.execute(
        select(FirecrawlDiscoveryRunModel)
        .where(FirecrawlDiscoveryRunModel.run_id == run_id)
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        db.rollback()
        raise RuntimeError("Discovery run disappeared during ownership claim")
    if row.plan_hash != plan_hash:
        db.rollback()
        raise DiscoveryPlanConflict(f"run_id {run_id!r} is already bound to a different plan")

    if row.status in {"completed", "failed", "disabled", "quota_stopped", "payment_required"}:
        db.commit()
        return "replay", row
    if row.status == "unknown":
        # Unknown is an authoritative parent outcome. Reconcile any committed
        # discovery receipts before replaying the durable receipt, even if the
        # run row is no longer in the running state.
        reconcile_discovery_reservations(db, run_id=run_id)
        execution_ids = db.execute(
            text(
                "SELECT id FROM search_execution "
                "WHERE cycle_id=:run_id AND provider_name='firecrawl' ORDER BY id"
            ),
            {"run_id": run_id},
        ).scalars().all()
        reserved_credits = _reserved_run_credits(db, execution_ids)
        receipt = dict(row.receipt or {})
        receipt.update(
            {
                "run_id": run_id,
                "status": "unknown",
                "credits_consumed": reserved_credits,
                "credits_reserved": reserved_credits,
                "execution_ids": execution_ids,
            }
        )
        row.receipt = receipt
        row.completed_at = row.completed_at or datetime.now(timezone.utc)
        db.commit()
        return "unknown", row

    # A worker crash can leave a running row forever.  The lease, not
    # started_at, is the ownership evidence.  A child lease can keep a
    # partial run live even when the parent lease has gone stale.
    owner_live = (
        row.status == "running"
        and row.owner_token is not None
        and row.owner_expires_at is not None
        and row.owner_expires_at > datetime.now(timezone.utc)
    )
    if owner_live:
        db.rollback()
        return "in_progress", row
    if row.status == "running":
        child_live = db.execute(text("""
            SELECT 1 FROM search_execution
            WHERE cycle_id=:run_id AND status IN ('selected', 'started')
              AND ownership_token IS NOT NULL
              AND ownership_expires_at > clock_timestamp()
            LIMIT 1 FOR UPDATE
        """), {"run_id": run_id}).first()
        if child_live is not None:
            db.rollback()
            return "in_progress", row
        reconcile_discovery_reservations(db, run_id=run_id)
        has_staged_results = db.execute(text("""
            SELECT EXISTS (
                SELECT 1 FROM firecrawl_operations o
                JOIN search_execution e ON e.id=o.execution_id
                WHERE e.cycle_id=:run_id AND o.operation='discovery'
                  AND o.result_state IN ('staged', 'consumed')
            )
        """), {"run_id": run_id}).scalar()
        if has_staged_results:
            row.owner_token = owner_token
            row.owner_expires_at = owner_expires_at
            row.started_at = datetime.now(timezone.utc)
            row.completed_at = None
            db.execute(text("""
                UPDATE search_execution
                SET ownership_token=:owner_token, ownership_expires_at=:owner_expires_at
                WHERE cycle_id=:run_id AND status IN ('selected', 'started')
            """), {
                "owner_token": owner_token,
                "owner_expires_at": owner_expires_at,
                "run_id": run_id,
            })
            db.commit()
            return "claimed", row
        # The run id is also the search execution cycle id.  Reconcile paid
        # obligations before publishing the unknown receipt so the takeover
        # and its reservation/operation evidence share one transaction.
        execution_ids = db.execute(
            text(
                "SELECT id FROM search_execution "
                "WHERE cycle_id=:run_id AND provider_name='firecrawl' ORDER BY id"
            ),
            {"run_id": run_id},
        ).scalars().all()
        reserved_credits = _reserved_run_credits(db, execution_ids)
        row.status = "unknown"
        row.completed_at = datetime.now(timezone.utc)
        row.receipt = {
            "run_id": run_id,
            "status": "unknown",
            "query_count": len(execution_ids),
            "attempted_queries": len(execution_ids),
            "successful_queries": 0,
            "reason": "Previous worker ownership expired; external outcome is unknown",
            "credits_consumed": reserved_credits,
            "credits_reserved": reserved_credits,
            "execution_ids": execution_ids,
        }
        db.commit()
        return "unknown", row

    db.rollback()
    return "in_progress", row


def _finish_discovery_run(
    db: Session,
    run_id: str,
    telemetry: FirecrawlDiscoveryTelemetry,
    owner_token: str,
) -> None:
    receipt = telemetry.model_dump(mode="json")
    updated = db.execute(
        text("""
            UPDATE firecrawl_discovery_runs
            SET status = :status, receipt = CAST(:receipt AS jsonb), completed_at = clock_timestamp()
            WHERE run_id = :run_id AND status = 'running'
              AND owner_token = :owner_token
              AND owner_expires_at > clock_timestamp()
        """),
        {
            "run_id": run_id,
            "owner_token": owner_token,
            "status": telemetry.status,
            "receipt": json.dumps(receipt),
        },
    )
    if updated.rowcount != 1:
        db.rollback()
        raise DiscoveryOwnershipLost(f"Discovery run {run_id} ownership was lost before completion")
    db.commit()


def _mark_discovery_unknown(db: Session, run_id: str, reason: str, owner_token: str) -> None:
    try:
        db.rollback()
        receipt = {
            "run_id": run_id,
            "status": "unknown",
            "query_count": 0,
            "attempted_queries": 0,
            "successful_queries": 0,
            "reason": reason[:500],
            "execution_ids": [],
        }
        db.execute(
            text("""
                UPDATE firecrawl_discovery_runs
                SET status = 'unknown', receipt = CAST(:receipt AS jsonb), completed_at = clock_timestamp()
                WHERE run_id = :run_id
                  AND status = 'running'
                  AND owner_token = :owner_token
                  AND owner_expires_at > clock_timestamp()
            """),
            {"run_id": run_id, "owner_token": owner_token, "receipt": json.dumps(receipt)},
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Unable to persist unknown Firecrawl discovery run run_id=%s", run_id)


def _recover_staged_discovery_results(db: Session, run_id: str) -> list[int]:
    """Replay every staged query for a run before ownership can retry it."""
    operation_rows = db.execute(text("""
        SELECT o.operation_key, o.execution_id, e.query_variant, e.retrieval_location
        FROM firecrawl_operations o
        JOIN search_execution e ON e.id=o.execution_id
        WHERE e.cycle_id=:run_id AND o.operation='discovery'
          AND o.result_state IN ('staged', 'consumed')
        ORDER BY o.id
    """), {"run_id": run_id}).mappings().all()
    recovered: list[int] = []
    for row in operation_rows:
        query = JobSearchQuery(
            keywords=row["query_variant"] or "",
            location=row["retrieval_location"] or "",
            page=1,
            page_size=10,
        )
        result = recover_staged_firecrawl_result(
            db,
            operation_key=row["operation_key"],
            execution_id=row["execution_id"],
            query=query,
        )
        if result.found and not result.blocked:
            recovered.append(row["execution_id"])
    return recovered


def _record_discovery_operation_if_missing(
    db: Session,
    *,
    operation_key: str,
    execution_id: int,
    cost_units: int,
    status: str,
    run_id: str,
    query_key: str,
) -> None:
    """Record compatibility telemetry only when T2 did not already do so.

    T2 owns reservation settlement and writes the same operation key.  In
    particular, never overwrite ``unknown_after_crash`` with a later summary
    status from this orchestration layer.
    """
    existing = db.execute(
        text("SELECT 1 FROM firecrawl_operations WHERE operation_key = :operation_key LIMIT 1"),
        {"operation_key": operation_key},
    ).scalar()
    if existing:
        return
    record_firecrawl_operation(
        operation="discovery",
        cost_units=cost_units,
        status=status,
        execution_id=execution_id,
        operation_key=operation_key,
        metadata={"run_id": run_id, "query_key": query_key, "operation_key": operation_key},
    )


def _reserved_run_credits(db: Session, execution_ids: list[int]) -> int:
    """Read T2's committed receipts, including paid failures and unknowns.

    Billing remains in the shared reservation service. A provider exception
    (notably 402) does not imply the already committed reservation disappeared.
    """
    if not execution_ids:
        return 0
    receipts = db.execute(
        select(ProviderReservationModel.cost_units).where(
            ProviderReservationModel.execution_id.in_(execution_ids),
            ProviderReservationModel.provider_name == "firecrawl",
        )
    ).scalars().all()
    return sum(receipts)


def _execute_discovery_run_body(
    db: Session,
    plan: FirecrawlDiscoveryPlan | None = None,
    force: bool = False,
    run_id: str | None = None,
    owner_token: str | None = None,
) -> FirecrawlDiscoveryTelemetry:
    max_queries = getattr(settings, "firecrawl_discovery_max_queries_per_run", 2)
    max_results = getattr(settings, "firecrawl_discovery_max_results_per_query", 10)
    max_credits = getattr(settings, "firecrawl_discovery_max_credits_per_run", 20)


    # Server-authoritative enabled check
    is_enabled = bool(getattr(settings, "firecrawl_discovery_enabled", False)) or force
    if not is_enabled:
        return FirecrawlDiscoveryTelemetry(
            run_id=run_id,
            status="disabled",
            query_count=0,
            attempted_queries=0,
            successful_queries=0,
            candidates_found=0,
            candidates_accepted=0,
            candidates_rejected=0,
            ingestion=IngestionStats(),
            credits_consumed=0,
            credits_reserved=0,
            estimated_credits=0,
            reason="Firecrawl discovery is disabled",
        )

    if not plan.queries:
        return FirecrawlDiscoveryTelemetry(
            run_id=run_id,
            status="completed",
            query_count=0,
            attempted_queries=0,
            successful_queries=0,
            candidates_found=0,
            candidates_accepted=0,
            candidates_rejected=0,
            ingestion=IngestionStats(),
            credits_consumed=0,
            credits_reserved=0,
            estimated_credits=0,
            reason=plan.reason or "No discovery queries to execute",
        )

    # Server-authoritative query limits and sanitization:
    # 1. Bounded to backend max_queries (never trust submitted plan.queries length)
    queries_to_execute = plan.queries[:max_queries]

    # 2. Server-authoritatively calculate estimated cost per query and cap to max_credits_per_run
    validated_queries: list[tuple[DiscoveryQuery, int, int]] = []
    total_estimated_credits = 0

    for q in queries_to_execute:
        # Normalize page_size strictly to backend max_results (default 10)
        norm_page_size = min(max(1, q.page_size), max_results)
        # Authoritative cost calculation (ignoring any client-supplied estimated_credits)
        cost_units = calculate_search_credits(norm_page_size)

        if total_estimated_credits + cost_units > max_credits:
            logger.info(
                "Planned query %s exceeds per-run credit limit (%s credits); stopping planning",
                q.keywords, max_credits
            )
            break

        validated_queries.append((q, norm_page_size, cost_units))
        total_estimated_credits += cost_units

    telemetry = FirecrawlDiscoveryTelemetry(
        run_id=run_id,
        status="completed",
        query_count=len(validated_queries),
        attempted_queries=0,
        successful_queries=0,
        candidates_found=0,
        candidates_accepted=0,
        candidates_rejected=0,
        ingestion=IngestionStats(),
        credits_consumed=0,
        credits_reserved=0,
        estimated_credits=total_estimated_credits,
    )

    if not validated_queries:
        telemetry.reason = "No queries within per-run credit cap"
        return telemetry

    try:
        if not owner_token or not run_id:
            raise DiscoveryOwnershipLost("Discovery execution requires an owner lease")
        lease_expires_at = renew_discovery_ownership(
            db, run_id=run_id, owner_token=owner_token,
        )
        provider = create_provider(ProviderName.FIRECRAWL)
    except ProviderConfigurationError as e:
        logger.error("Firecrawl provider configuration error: %s", e)
        telemetry.status = "failed"
        telemetry.reason = "Provider configuration error"
        return telemetry
    except DiscoveryOwnershipLost:
        raise
    except Exception as e:
        logger.exception("Failed to instantiate FirecrawlProvider: %s", e)
        telemetry.status = "failed"
        telemetry.reason = "Failed to instantiate provider"
        return telemetry

    for query_index, (q, norm_page_size, cost_units) in enumerate(validated_queries, start=1):
        lease_expires_at = renew_discovery_ownership(
            db, run_id=run_id, owner_token=owner_token,
        )
        if telemetry.credits_reserved + cost_units > max_credits:
            logger.info("Discovery run reached per-run credit limit (%s credits)", max_credits)
            telemetry.reason = f"Per-run credit limit of {max_credits} reached"
            break

        telemetry.attempted_queries += 1
        query = JobSearchQuery(
            keywords=q.keywords,
            location=q.location,
            page=1,
            page_size=norm_page_size,
        )

        # The logical query key is stable across workers and runs.  It gives
        # operators a durable operation identity without changing the shared
        # quota/reservation implementation owned by the ingestion service.
        query_key = _canonical_query_key(q.keywords, q.location, norm_page_size)
        db.execute(
            insert(FirecrawlQueryStateModel).values(
                query_key=query_key,
                keywords=q.keywords.strip(),
                location=q.location.strip(),
                original_keywords=q.keywords,
                original_location=q.location,
                last_planned_at=datetime.now(timezone.utc),
            ).on_conflict_do_update(
                index_elements=[FirecrawlQueryStateModel.query_key],
                set_={
                    "keywords": q.keywords.strip(),
                    "location": q.location.strip(),
                    "original_keywords": q.keywords,
                    "original_location": q.location,
                    "last_planned_at": datetime.now(timezone.utc),
                },
            )
        )
        db.commit()

        now_dt = datetime.now(timezone.utc)
        query_hash = query_key[:8]
        candidate_id = f"fc_disc_{run_id}_{query_index}_{query_hash}"
        execution = db.execute(
            select(SearchExecutionModel).where(SearchExecutionModel.candidate_id == candidate_id)
        ).scalar_one_or_none()
        if execution is None:
            execution = SearchExecutionModel(
                candidate_id=candidate_id,
                cycle_id=run_id,
                status="selected",
                provider_name="firecrawl",
                query_variant=q.keywords,
                retrieval_location=q.location,
                ownership_token=owner_token,
                ownership_expires_at=lease_expires_at,
                selected_at=now_dt,
            )
            db.add(execution)
            db.commit()
        # T2's ingestion lifecycle derives its reservation key from the
        # selected execution. Reuse that exact key for discovery telemetry so
        # the reservation receipt and operation receipt remain one durable
        # operation (without duplicating quota/reservation logic here).
        operation_key = f"discovery:firecrawl:{execution.id}"
        telemetry.execution_ids.append(execution.id)

        try:
            if execution.status in {"selected", "started"}:
                lease_expires_at = renew_discovery_ownership(
                    db,
                    run_id=run_id,
                    owner_token=owner_token,
                    execution_id=execution.id,
                )
            existing_operation = db.execute(text("""
                SELECT status, result_state FROM firecrawl_operations
                WHERE operation_key=:operation_key
            """), {"operation_key": operation_key}).mappings().first()
            if existing_operation and existing_operation["result_state"] in {"staged", "consumed"}:
                from app.services.firecrawl_result_staging import recover_staged_firecrawl_result, request_fingerprint
                recovered = recover_staged_firecrawl_result(
                    db,
                    operation_key=operation_key,
                    execution_id=execution.id,
                    query=query,
                    discovery_run_id=run_id,
                    discovery_owner_token=owner_token,
                    ownership_check=lambda: assert_discovery_ownership(
                        db,
                        run_id=run_id,
                        owner_token=owner_token,
                        execution_id=execution.id,
                    ),
                    request_fp=request_fingerprint(
                        provider_name="firecrawl", query=query, execution_id=execution.id,
                    ),
                )
                if recovered.blocked or not recovered.found:
                    telemetry.status = "unknown"
                    telemetry.reason = "Staged discovery result could not be safely recovered"
                    break
                result, job_ids = recovered.result, list(recovered.job_ids)
            elif existing_operation and existing_operation["status"] == "unknown_after_crash":
                telemetry.status = "unknown"
                telemetry.reason = "Previous discovery provider outcome is unknown; no automatic retry"
                break
            elif execution.status == "succeeded":
                result = IngestionResult(
                    provider="firecrawl",
                    fetched=execution.jobs_fetched or 0,
                    created=execution.jobs_created or 0,
                    duplicates=execution.jobs_duplicates or 0,
                    invalid=execution.jobs_invalid or 0,
                )
                job_ids = []
            elif execution.status == "failed":
                result = IngestionResult(provider="firecrawl", failed=1)
                job_ids = []
            else:
                result, job_ids = run_ingestion(
                    db,
                    "firecrawl",
                    provider,
                    query,
                    execution_id=execution.id,
                    operation_key=operation_key,
                    discovery_run_id=run_id,
                    discovery_owner_token=owner_token,
                    ownership_check=lambda: assert_discovery_ownership(
                        db,
                        run_id=run_id,
                        owner_token=owner_token,
                        execution_id=execution.id,
                    ),
                )
            db.execute(
                update(FirecrawlQueryStateModel)
                .where(FirecrawlQueryStateModel.query_key == query_key)
                .values(last_executed_at=datetime.now(timezone.utc))
            )
            if result.failed == 0:
                _record_discovery_operation_if_missing(
                    db, operation_key=operation_key, execution_id=execution.id,
                    cost_units=cost_units, status="success", run_id=run_id, query_key=query_key,
                )
                telemetry.successful_queries += 1
                search_telem = getattr(provider, "last_search_telemetry", {})
                if isinstance(search_telem, dict) and search_telem:
                    telemetry.candidates_found += search_telem.get("candidates_found", result.fetched)
                    telemetry.candidates_accepted += result.created
                    prov_rejected = search_telem.get("candidates_rejected", 0)
                    telemetry.candidates_rejected += prov_rejected + result.duplicates + result.invalid
                    telemetry.validated_individual += search_telem.get("candidates_accepted", result.fetched)
                    rejections_dict = search_telem.get("rejections_by_reason")
                    if isinstance(rejections_dict, dict):
                        for r_reason, r_cnt in rejections_dict.items():
                            telemetry.rejections_by_reason[r_reason] = telemetry.rejections_by_reason.get(r_reason, 0) + r_cnt

                        if rejections_dict:
                            db.execute(text("""
                                UPDATE search_execution
                                SET error_message = :rejection_json
                                WHERE id = :eid
                            """), {
                                "eid": execution.id,
                                "rejection_json": json.dumps({"rejections_by_reason": rejections_dict})
                            })
                            db.commit()
                else:
                    telemetry.candidates_found += result.fetched
                    telemetry.candidates_accepted += result.created
                    telemetry.candidates_rejected += (result.duplicates + result.invalid)
                    telemetry.validated_individual += result.fetched

                if result.duplicates > 0:
                    telemetry.rejections_by_reason["duplicate_in_db"] = telemetry.rejections_by_reason.get("duplicate_in_db", 0) + result.duplicates
                if result.invalid > 0:
                    telemetry.rejections_by_reason["invalid_persistence"] = telemetry.rejections_by_reason.get("invalid_persistence", 0) + result.invalid

                telemetry.ingestion.created += result.created
                telemetry.ingestion.duplicates += result.duplicates
                telemetry.ingestion.invalid += result.invalid
            else:
                _record_discovery_operation_if_missing(
                    db, operation_key=operation_key, execution_id=execution.id,
                    cost_units=cost_units, status="failed", run_id=run_id, query_key=query_key,
                )
                telemetry.ingestion.failed += result.failed
                logger.warning("Discovery query failed for %s: %s", q.keywords, result.failed)
        except DiscoveryOwnershipLost:
            raise
        except RateLimitExceeded:
            logger.warning("Firecrawl monthly quota limit reached during discovery run")
            telemetry.status = "quota_stopped"
            telemetry.reason = "Monthly quota cap reached"
            # Quota was denied, so ZERO credits were reserved for this request
            break
        except PaymentRequired:
            telemetry.status = "payment_required"
            telemetry.reason = "Firecrawl payment required"
            break
        except ProviderSettlementError:
            # The provider result was returned, but its durable reservation
            # settlement could not be confirmed.  Keep the run and operation
            # fail-closed; a replay must discover this receipt rather than
            # dispatching Firecrawl a second time.
            telemetry.status = "unknown"
            telemetry.reason = "Provider reservation settlement is unresolved"
            _record_discovery_operation_if_missing(
                db, operation_key=operation_key, execution_id=execution.id,
                cost_units=cost_units, status="in_flight", run_id=run_id, query_key=query_key,
            )
            telemetry.ingestion.failed += 1
            break
        except DatabaseUnavailable:
            logger.error("Database unavailable during discovery query %s", q.keywords)
            telemetry.status = "failed"
            telemetry.reason = "Database unavailable"
            break
        except Exception as e:
            logger.warning("Firecrawl discovery query error for %s: %s", q.keywords, e)
            telemetry.ingestion.failed += 1
        finally:
            # This runs on every exit, including PaymentRequired. Use receipt
            # amounts rather than inferring a charge from a successful return.
            reserved = _reserved_run_credits(db, telemetry.execution_ids)
            telemetry.credits_consumed = reserved
            telemetry.credits_reserved = reserved

    return telemetry


def execute_discovery_run(
    db: Session,
    plan: FirecrawlDiscoveryPlan | None = None,
    force: bool = False,
    run_id: str | None = None,
) -> FirecrawlDiscoveryTelemetry:
    """Execute one durably-owned discovery run, or replay its receipt."""
    run_id = run_id or (plan.run_id if plan is not None else None) or f"fc_run_{uuid.uuid4().hex[:16]}"
    if plan is None:
        plan = plan_discovery_queries(db, force=force, run_id=run_id)
    elif plan.run_id != run_id:
        plan = plan.model_copy(update={"run_id": run_id})

    state, row = _claim_discovery_run(db, run_id, _plan_hash(plan))
    if state == "replay" and row is not None:
        receipt = _telemetry_from_receipt(run_id, row.receipt)
        if receipt is not None:
            return receipt
        return FirecrawlDiscoveryTelemetry(
            run_id=run_id,
            status="unknown",
            reason="Stored run outcome is missing or malformed; no automatic retry",
        )
    if state == "in_progress":
        return FirecrawlDiscoveryTelemetry(
            run_id=run_id,
            status="running",
            reason="A worker already owns this run; no duplicate provider request was made",
        )
    if state == "unknown" and row is not None:
        try:
            _recover_staged_discovery_results(db, run_id)
            execution_ids = db.execute(text(
                "SELECT id FROM search_execution "
                "WHERE cycle_id=:run_id AND provider_name='firecrawl' ORDER BY id"
            ), {"run_id": run_id}).scalars().all()
            reserved_credits = _reserved_run_credits(db, execution_ids)
            receipt_data = dict(row.receipt or {})
            receipt_data.update({
                "run_id": run_id,
                "status": "unknown",
                "credits_consumed": reserved_credits,
                "credits_reserved": reserved_credits,
                "execution_ids": execution_ids,
            })
            row.receipt = receipt_data
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Unable to recover staged unknown Firecrawl run run_id=%s", run_id)
        receipt = _telemetry_from_receipt(run_id, row.receipt)
        return receipt or FirecrawlDiscoveryTelemetry(
            run_id=run_id,
            status="unknown",
            reason="Previous worker outcome is unknown; no automatic retry",
        )

    try:
        # The body performs per-query recovery only after this ownership claim.
        # A fresh duplicate caller returned ``in_progress`` above and cannot
        # consume a first worker's staged query or publish a premature receipt.
        if row is None or not row.owner_token:
            raise DiscoveryOwnershipLost(f"Discovery run {run_id} has no owner lease")
        telemetry = _execute_discovery_run_body(
            db,
            plan=plan,
            force=force,
            run_id=run_id,
            owner_token=row.owner_token,
        )
        telemetry.run_id = run_id
        _finish_discovery_run(db, run_id, telemetry, row.owner_token)
        return telemetry
    except Exception as exc:
        _mark_discovery_unknown(
            db,
            run_id,
            f"Worker failure; external outcome is unknown: {exc}",
            row.owner_token if row is not None else "",
        )
        raise
