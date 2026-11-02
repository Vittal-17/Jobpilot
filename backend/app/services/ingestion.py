from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
from datetime import datetime, timezone
import logging
import uuid
import json
from collections.abc import Callable, Mapping
from app.providers.base import JobProvider
from app.schemas.job_search import JobSearchQuery, IngestionResult
from app.db.repository.job_repository import save_job
from app.services.firecrawl_result_staging import FirecrawlOperationConflict
from app.services.firecrawl_quota import DiscoveryOwnershipLost

logger = logging.getLogger(__name__)

class RateLimitExceeded(Exception):
    pass

class DatabaseUnavailable(Exception):
    pass

class InvalidExecutionTransition(Exception):
    pass

class PaymentRequired(Exception):
    """Provider account is exhausted; unlike telemetry this is a durable latch."""
    pass


class ProviderSettlementError(RuntimeError):
    """The provider result exists, but its reservation receipt was not durably settled."""


def _settle_provider_receipt(receipt, status: str, *, db: Session, **kwargs):
    """Settle and fail closed when the durable receipt cannot be confirmed."""
    from app.services.firecrawl_quota import settle_provider_reservation
    try:
        settled = settle_provider_reservation(receipt, status, db=db, **kwargs)
    except Exception as exc:
        logger.exception(
            "Unable to settle provider reservation id=%s status=%s",
            getattr(receipt, "reservation_id", receipt), status,
        )
        raise ProviderSettlementError(
            f"Provider reservation settlement failed for {getattr(receipt, 'reservation_id', receipt)}"
        ) from exc
    if settled is None or getattr(settled, "status", None) != status:
        raise ProviderSettlementError(
            f"Provider reservation settlement was not durable for "
            f"{getattr(receipt, 'reservation_id', receipt)} (expected {status})"
        )
    return settled


def _enrichment_lease_is_live(db: Session, job_id: int, lease_token: str) -> bool:
    """Check and lock the enrichment claim used to authorize paid work."""
    row = db.execute(
        text(
            "SELECT 1 FROM job_enrichments "
            "WHERE job_id = :job_id AND status = 'in_progress' "
            "AND lease_token = :lease_token "
            "AND lease_expires_at > clock_timestamp() FOR UPDATE"
        ),
        {"job_id": job_id, "lease_token": lease_token},
    ).scalar()
    return bool(row)


def _transition_execution(
    db: Session,
    execution_id: int,
    expected_status: str,
    new_status: str,
    values: dict,
) -> None:

    ALLOWED_COLUMNS = {
        "started_at", "completed_at", "provider_name",
        "jobs_fetched", "jobs_created", "jobs_duplicates",
        "jobs_invalid", "error_message"
    }

    assignments = ["status = :new_status"]
    params = {
        "execution_id": execution_id,
        "expected_status": expected_status,
        "new_status": new_status,
    }
    for column, value in values.items():
        if column not in ALLOWED_COLUMNS:
            raise ValueError(f"Unsafe transition column: {column}")
        assignments.append(f"{column} = :{column}")
        params[column] = value


    result = db.execute(
        text(
            f"UPDATE search_execution SET {', '.join(assignments)} "
            "WHERE id = :execution_id AND status = :expected_status"
        ),
        params,
    )
    if result.rowcount != 1:
        raise InvalidExecutionTransition(
            f"Execution {execution_id} is not in {expected_status} state"
        )


def _fail_execution(db: Session, execution_id: int, error_message: str) -> None:
    _transition_execution(
        db,
        execution_id,
        "started",
        "failed",
        {
            "completed_at": datetime.now(timezone.utc),
            "error_message": error_message,
        },
    )
    db.commit()


def _best_effort_fail_execution(
    db: Session, execution_id: int, error_message: str
) -> None:
    try:
        _fail_execution(db, execution_id, error_message)
    except Exception:
        db.rollback()
        logger.exception(
            "Unable to record failed execution execution_id=%s", execution_id
        )

def reserve_provider_request(
    db: Session,
    provider_name: str,
    reference_time: datetime | None = None,
    cost_units: int = 1,
    *, execution_id: int | None = None, job_id: int | None = None,
    lease_token: str | None = None, reservation_id: str | None = None,
    operation_key: str | None = None, request_fingerprint: str | None = None,
):
    """Atomically authorize and reserve a provider request, returning a receipt."""
    from app.db.models.execution_outcome import ProviderReservationModel
    from app.services.firecrawl_quota import ProviderReservationReceipt, independent_bind
    supplied_operation_key = operation_key or reservation_id
    operation_key = supplied_operation_key or f"{provider_name}:{uuid.uuid4()}"
    reservation_id = reservation_id or str(uuid.uuid4())

    def record_quota_denial() -> None:
        if provider_name not in ("firecrawl", "firecrawl_monthly"):
            return
        from app.services.firecrawl_quota import record_firecrawl_operation

        record_firecrawl_operation(
            operation="discovery" if provider_name == "firecrawl" else "enrichment",
            cost_units=cost_units,
            status="denied",
            execution_id=execution_id,
            job_id=job_id,
            operation_key=operation_key,
        )

    if cost_units <= 0:
        logger.warning(
            "Non-positive cost_units=%s requested for provider %s, blocking reservation.",
            cost_units,
            provider_name,
        )
        return None

    if provider_name in ("firecrawl", "firecrawl_monthly"):
        from app.core.config import settings
        if not getattr(settings, "firecrawl_enabled", True):
            logger.warning("Firecrawl is disabled, blocking reservation.")
            return None
        if not getattr(settings, "firecrawl_api_key", None):
            logger.warning("Firecrawl API key is not configured, blocking reservation.")
            return None

    from app.services.quota_policy import get_provider_policy

    try:
        policy = get_provider_policy(provider_name)
    except ValueError as e:
        logger.error(str(e))
        raise ValueError(f"ConfigurationError: {str(e)}")

    minute_limit = policy.get_effective_limit('minute')
    daily_limit = policy.get_effective_limit('daily')
    weekly_limit = policy.get_effective_limit('weekly')
    monthly_limit = policy.get_effective_limit('monthly')
    lifetime_limit = policy.get_effective_limit('lifetime')

    if daily_limit is not None and daily_limit <= 0:
        logger.warning(f"Provider {provider_name} has non-positive daily limit, blocking request.")
        record_quota_denial()
        return None

    if minute_limit is not None and minute_limit <= 0:
        logger.warning(f"Provider {provider_name} has non-positive minute limit, blocking request.")
        record_quota_denial()
        return None

    if weekly_limit is not None and weekly_limit <= 0:
        logger.warning(f"Provider {provider_name} has non-positive weekly limit, blocking request.")
        record_quota_denial()
        return None

    if monthly_limit is not None and monthly_limit <= 0:
        logger.warning(f"Provider {provider_name} has non-positive monthly limit, blocking request.")
        record_quota_denial()
        return None

    if lifetime_limit is not None and lifetime_limit <= 0:
        logger.warning(f"Provider {provider_name} has non-positive lifetime limit, blocking request.")
        record_quota_denial()
        return None

    now_utc = reference_time or datetime.now(timezone.utc)
    today = now_utc.date()
    current_minute = now_utc.replace(second=0, microsecond=0)
    try:
        from app.services.firecrawl_quota import ensure_firecrawl_budget, policy_matches
        with Session(independent_bind(db)) as quota_db:
            # No usage mutation happens before the live ownership fence.
            if job_id is not None:
                if not lease_token or not _enrichment_lease_is_live(quota_db, job_id, lease_token):
                    quota_db.rollback()
                    return None
                # A reclaimed lease must never purchase a second paid request
                # while an earlier receipt is unresolved. A stale reserved
                # receipt is reconciled only after its original lease is no
                # longer live; usage remains consumed and is never refunded.
                from app.services.firecrawl_quota import reconcile_enrichment_reservations
                reconcile_enrichment_reservations(quota_db, job_id)
                unresolved = quota_db.execute(text("""
                    SELECT 1 FROM provider_reservations
                        WHERE job_id=:job_id AND (
                            status IN ('reserved','in_flight','unknown_after_crash')
                            OR (status='failed' AND (billing_metadata->'external_call') IS DISTINCT FROM 'false'::jsonb)
                        )
                          AND operation_key <> :operation_key
                    FOR UPDATE
                """), {"job_id": job_id, "operation_key": operation_key}).scalar()
                if unresolved:
                    quota_db.commit()
                    return None
            elif execution_id is not None:
                owned_execution = quota_db.execute(text(
                    "SELECT 1 FROM search_execution WHERE id=:eid AND status='started' "
                    "AND provider_name=:provider AND started_at > clock_timestamp() - INTERVAL '15 minutes' "
                    "FOR UPDATE"
                ), {"eid": execution_id, "provider": provider_name}).scalar()
                if not owned_execution:
                    quota_db.rollback()
                    return None

            # Serialize explicit request keys even for providers whose policy
            # does not otherwise require a weekly/monthly lock.
            if supplied_operation_key:
                quota_db.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext('provider_operation'), hashtext(:key))"),
                    {"key": operation_key},
                )
                existing = quota_db.execute(
                    text(
                        "SELECT id, operation_key, provider_name, cost_units, usage_date, "
                        "execution_id, job_id, lease_token, status, request_fingerprint FROM provider_reservations "
                        "WHERE operation_key = :operation_key"
                    ),
                    {"operation_key": operation_key},
                ).mappings().first()
                if isinstance(existing, Mapping):
                    if (existing["provider_name"], existing["cost_units"], existing["execution_id"], existing["job_id"], existing["lease_token"]) != (provider_name, cost_units, execution_id, job_id, lease_token):
                        raise ValueError("Provider operation key conflicts with reservation parameters")
                    if request_fingerprint is not None and existing["request_fingerprint"] != request_fingerprint:
                        raise FirecrawlOperationConflict("operation key is bound to a different request")
                    return ProviderReservationReceipt.from_mapping(existing)

            lock_key = "firecrawl" if provider_name in ("firecrawl", "firecrawl_monthly") else provider_name
            if weekly_limit is not None or monthly_limit is not None or provider_name in ("firecrawl", "firecrawl_monthly"):
                quota_db.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext('provider_quota'), hashtext(:p))"),
                    {"p": lock_key},
                )

            if provider_name in ("firecrawl", "firecrawl_monthly"):
                budget=ensure_firecrawl_budget(quota_db,today)
                if budget["payment_required"] or not policy_matches(budget):
                    quota_db.commit()
                    record_quota_denial()
                    return None

                stmt_monthly_check = text("""
                    SELECT COALESCE(SUM(request_count), 0)
                    FROM provider_usage
                    WHERE provider_name IN ('firecrawl', 'firecrawl_monthly')
                      AND usage_date >= CAST(DATE_TRUNC('month', CAST(:d AS date)) AS date)
                      AND usage_date < CAST(DATE_TRUNC('month', CAST(:d AS date)) + INTERVAL '1 month' AS date)
                """)
                current_monthly = quota_db.execute(stmt_monthly_check, {"d": today}).scalar() or 0
                monthly_limit=min(budget["automation_cap"],budget["account_budget"]-budget["protected_reserve"])
                if current_monthly + cost_units > monthly_limit:
                    quota_db.rollback()
                    record_quota_denial()
                    return None

                target_date = today.replace(day=1) if provider_name == "firecrawl_monthly" else today
                stmt_firecrawl_record = text("""
                    INSERT INTO provider_usage (provider_name, usage_date, request_count)
                    VALUES (:p, :d, :cost)
                    ON CONFLICT (provider_name, usage_date)
                    DO UPDATE SET request_count = provider_usage.request_count + :cost
                    RETURNING request_count
                """)
                quota_db.execute(stmt_firecrawl_record, {"p": provider_name, "d": target_date, "cost": cost_units})
                quota_db.add(ProviderReservationModel(
                    id=reservation_id,
                    operation_key=operation_key,
                    request_fingerprint=request_fingerprint,
                    provider_name=provider_name,
                    cost_units=cost_units,
                    usage_date=today,
                    execution_id=execution_id,
                    job_id=job_id,
                    lease_token=lease_token,
                    status="reserved",
                ))
                quota_db.commit()
                return ProviderReservationReceipt(
                    reservation_id=reservation_id,
                    operation_key=operation_key,
                    provider_name=provider_name,
                    cost_units=cost_units,
                    usage_date=today,
                    execution_id=execution_id,
                    job_id=job_id,
                    lease_token=lease_token,
                )

            if minute_limit is not None:
                stmt_minute = text("""
                    INSERT INTO provider_minute_usage (provider_name, usage_minute, request_count)
                    VALUES (:p, :m, :cost)
                    ON CONFLICT (provider_name, usage_minute)
                    DO UPDATE SET request_count = provider_minute_usage.request_count + :cost
                    RETURNING request_count
                """)
                res_minute = quota_db.execute(stmt_minute, {"p": provider_name, "m": current_minute, "cost": cost_units}).scalar()
                if res_minute > minute_limit:
                    quota_db.rollback()
                    return None

            if daily_limit is not None or weekly_limit is not None or monthly_limit is not None:
                stmt = text("""
                    INSERT INTO provider_usage (provider_name, usage_date, request_count)
                    VALUES (:p, :d, :cost)
                    ON CONFLICT (provider_name, usage_date)
                    DO UPDATE SET request_count = provider_usage.request_count + :cost
                    RETURNING request_count
                """)
                res_daily = quota_db.execute(stmt, {"p": provider_name, "d": today, "cost": cost_units}).scalar()
                if daily_limit is not None and res_daily > daily_limit:
                    quota_db.rollback()
                    return None

                if weekly_limit is not None:
                    stmt_weekly = text("""
                        SELECT COALESCE(SUM(request_count), 0)
                        FROM provider_usage
                        WHERE provider_name = :p
                          AND usage_date >= CAST(DATE_TRUNC('week', CAST(:d AS date)) AS date)
                          AND usage_date < CAST(DATE_TRUNC('week', CAST(:d AS date)) + INTERVAL '7 days' AS date)
                    """)
                    res_weekly = quota_db.execute(stmt_weekly, {"p": provider_name, "d": today}).scalar()
                    if res_weekly > weekly_limit:
                        quota_db.rollback()
                        return None

                if monthly_limit is not None:
                    stmt_monthly = text("""
                        SELECT COALESCE(SUM(request_count), 0)
                        FROM provider_usage
                        WHERE provider_name = :p
                          AND usage_date >= CAST(DATE_TRUNC('month', CAST(:d AS date)) AS date)
                          AND usage_date < CAST(DATE_TRUNC('month', CAST(:d AS date)) + INTERVAL '1 month' AS date)
                    """)
                    res_monthly = quota_db.execute(stmt_monthly, {"p": provider_name, "d": today}).scalar()
                    if res_monthly > monthly_limit:
                        quota_db.rollback()
                        return None

            if lifetime_limit is not None:
                stmt_lifetime = text("""
                    INSERT INTO provider_state (provider_name, lifetime_count)
                    VALUES (:p, :cost)
                    ON CONFLICT (provider_name)
                    DO UPDATE SET lifetime_count = provider_state.lifetime_count + :cost
                    RETURNING lifetime_count
                """)
                res_lifetime = quota_db.execute(stmt_lifetime, {"p": provider_name, "cost": cost_units}).scalar()
                if res_lifetime > lifetime_limit:
                    quota_db.rollback()
                    return None

            quota_db.add(ProviderReservationModel(
                id=reservation_id,
                operation_key=operation_key,
                request_fingerprint=request_fingerprint,
                provider_name=provider_name,
                cost_units=cost_units,
                usage_date=today,
                execution_id=execution_id,
                job_id=job_id,
                lease_token=lease_token,
                status="reserved",
            ))
            quota_db.commit()
            return ProviderReservationReceipt(
                reservation_id=reservation_id,
                operation_key=operation_key,
                provider_name=provider_name,
                cost_units=cost_units,
                usage_date=today,
                execution_id=execution_id,
                job_id=job_id,
                lease_token=lease_token,
            )
    except FirecrawlOperationConflict:
        raise
    except DiscoveryOwnershipLost:
        raise
    except Exception as e:
        logger.exception("Failed to acquire provider quota provider_name=%s", provider_name)
        try:
            # Real quota work uses an independent Engine transaction; do not
            # roll back a caller-owned transaction as a helper side effect.
            if independent_bind(db) is db:
                db.rollback()
        except Exception:
            logger.debug("Unable to roll back caller quota session", exc_info=True)
        raise DatabaseUnavailable("Quota database unavailable")


def acquire_provider_request_slot(
    db: Session,
    provider_name: str,
    reference_time: datetime | None = None,
    cost_units: int = 1,
    *, execution_id: int | None = None, job_id: int | None = None,
    lease_token: str | None = None, reservation_id: str | None = None,
    operation_key: str | None = None,
) -> bool:
    """Bool-compatible facade retained for existing callers and tests."""
    from app.services.firecrawl_quota import ProviderReservationReceipt

    receipt = reserve_provider_request(
        db,
        provider_name,
        reference_time,
        cost_units,
        execution_id=execution_id,
        job_id=job_id,
        lease_token=lease_token,
        reservation_id=reservation_id,
        operation_key=operation_key,
    )
    return isinstance(receipt, ProviderReservationReceipt)

def run_ingestion(
    db: Session,
    provider_name: str,
    provider: JobProvider,
    query: JobSearchQuery,
    execution_id: int | None = None,
    *,
    operation_key: str | None = None,
    discovery_run_id: str | None = None,
    discovery_owner_token: str | None = None,
    ownership_check: Callable[[], None] | None = None,
) -> tuple[IngestionResult, list[int]]:
    result = IngestionResult(provider=provider_name)
    job_ids = []

    prov_key = provider_name.value if hasattr(provider_name, "value") else str(provider_name)
    request_fp = None
    if prov_key in ("firecrawl", "firecrawl_monthly"):
        from app.services.firecrawl_result_staging import request_fingerprint
        request_fp = request_fingerprint(
            provider_name=prov_key, query=query, execution_id=execution_id,
        )
    early_operation_key = (
        operation_key.strip() if isinstance(operation_key, str) and operation_key.strip() else None
    )
    if prov_key in ("firecrawl", "firecrawl_monthly") and early_operation_key:
        from app.services.firecrawl_result_staging import recover_staged_firecrawl_result
        if execution_id is None:
            from app.services.firecrawl_quota import reconcile_stale_direct_firecrawl_reservations
            reconcile_stale_direct_firecrawl_reservations(
                db,
                operation_key=early_operation_key,
            )
        if ownership_check is not None:
            ownership_check()
        early_recovery = recover_staged_firecrawl_result(
            db,
            operation_key=early_operation_key,
            execution_id=execution_id,
            query=query,
            discovery_run_id=discovery_run_id,
            discovery_owner_token=discovery_owner_token,
            ownership_check=ownership_check,
            request_fp=request_fp,
        )
        if early_recovery.found:
            if early_recovery.blocked:
                result.failed = 1
                return result, []
            return early_recovery.result, list(early_recovery.job_ids)

    if execution_id is not None:
        result_transition = db.execute(
            text(
                "UPDATE search_execution SET status = 'started', started_at = :started_at, "
                "provider_name = :provider_name "
                "WHERE id = :execution_id AND status = 'selected' "
                "AND (provider_name IS NULL OR provider_name = :provider_name)"
            ),
            {
                "execution_id": execution_id,
                "provider_name": provider_name,
                "started_at": datetime.now(timezone.utc),
            },
        )
        if result_transition.rowcount != 1:
            db.rollback()
            raise InvalidExecutionTransition(
                f"Execution {execution_id} is not routed to {provider_name}"
            )
        db.commit()

    from app.providers.exceptions import ProviderConfigurationError, ProviderError
    reservation_receipt = None
    cost_units = 0
    effective_operation_key: str | None = None
    try:
        if hasattr(provider, "validate_config"):
            provider.validate_config()

        if prov_key in ("firecrawl", "firecrawl_monthly"):
            from app.services.firecrawl_quota import calculate_search_credits
            from app.services.scraper.firecrawl import FirecrawlClient
            raw_limit = getattr(query, "page_size", 10) or 10
            limit = FirecrawlClient.bounded_search_limit(raw_limit)
            cost_units = calculate_search_credits(limit)
            supplied_operation_key = (
                operation_key.strip()
                if isinstance(operation_key, str) and operation_key.strip()
                else None
            )
            effective_operation_key = supplied_operation_key or f"direct:{prov_key}:{uuid.uuid4()}"
            # A durable staged result is the only safe replay path.  This is
            # intentionally before provider validation and reservation claim:
            # recovery must work even when a restarted worker has no provider
            # credentials available, and must never cross the paid boundary.
            if not early_operation_key:
                from app.services.firecrawl_result_staging import recover_staged_firecrawl_result
                recovered = recover_staged_firecrawl_result(
                    db,
                    operation_key=effective_operation_key,
                    execution_id=execution_id,
                    query=query,
                    discovery_run_id=discovery_run_id,
                    discovery_owner_token=discovery_owner_token,
                    ownership_check=ownership_check,
                    request_fp=request_fp,
                )
                if recovered.found:
                    if recovered.blocked:
                        if execution_id is not None:
                            _best_effort_fail_execution(db, execution_id, "Firecrawl operation cannot be safely recovered")
                        result.failed = 1
                        return result, []
                    return recovered.result, list(recovered.job_ids)
            if execution_id is None and not early_operation_key:
                from app.services.firecrawl_quota import reconcile_stale_direct_firecrawl_reservations
                reconcile_stale_direct_firecrawl_reservations(
                    db,
                    operation_key=supplied_operation_key,
                )
            if ownership_check is not None:
                ownership_check()
        else:
            # Non-Firecrawl providers retain the legacy quota contract.  They
            # are not paid operations governed by the Firecrawl reservation
            # state machine, and this preserves provider-policy test doubles
            # that intentionally replace the boolean quota facade.
            if not acquire_provider_request_slot(db, prov_key):
                logger.warning("Provider %s has no remaining request quota", prov_key)
                if execution_id is not None:
                    _fail_execution(db, execution_id, "Rate limit exceeded")
                raise RateLimitExceeded(f"Limit exceeded for {prov_key}")
            jobs = provider.search_jobs(query)
        if prov_key in ("firecrawl", "firecrawl_monthly"):
            if ownership_check is not None:
                ownership_check()
            reservation_receipt = reserve_provider_request(
                db,
                prov_key,
                cost_units=cost_units,
                execution_id=execution_id,
                operation_key=effective_operation_key,
                request_fingerprint=request_fp,
            )

            if reservation_receipt is None:
                logger.warning("Provider %s has no remaining request quota", prov_key)
                if execution_id is not None:
                    _fail_execution(db, execution_id, "Rate limit exceeded")
                raise RateLimitExceeded(f"Limit exceeded for {prov_key}")

            from app.services.firecrawl_quota import (
                cancel_reserved_provider_request,
                mark_provider_request_in_flight,
            )
            flight_receipt = mark_provider_request_in_flight(
                reservation_receipt,
                db=db,
                discovery_run_id=discovery_run_id,
                discovery_owner_token=discovery_owner_token,
            )
            if flight_receipt is None:
                # Existing/uncertain operation keys cannot authorize another paid
                # request.  The caller can return or reconcile the durable receipt.
                # Only cancel if the receipt is still reserved.  In particular,
                # never turn another worker's in-flight/terminal receipt into a
                # synthetic failed outcome.
                try:
                    if ownership_check is not None:
                        ownership_check()
                    cancel_reserved_provider_request(
                        reservation_receipt,
                        db=db,
                        reason="ownership_lost_or_already_dispatched",
                    )
                except Exception:
                    logger.exception(
                        "Unable to cancel unclaimed provider reservation id=%s",
                        reservation_receipt.reservation_id,
                    )
                if execution_id is not None:
                    _fail_execution(db, execution_id, "Provider operation already dispatched")
                result.failed = 1
                return result, job_ids
            reservation_receipt = flight_receipt
            if ownership_check is not None:
                ownership_check()
            jobs = provider.search_jobs(query)
            # The provider response is the paid evidence.  Persist a bounded,
            # normalized, hashed copy before settlement so a crash at any
            # later point can recover locally without another provider call.
            from app.services.firecrawl_result_staging import (
                FirecrawlResultError,
                recover_staged_firecrawl_result,
                stage_firecrawl_result,
            )
            try:
                stage_firecrawl_result(
                    db,
                    operation_key=effective_operation_key,
                    operation="discovery" if execution_id is not None else "discovery",
                    cost_units=cost_units,
                    execution_id=execution_id,
                    reservation_id=reservation_receipt.reservation_id,
                    jobs=jobs,
                    discovery_run_id=discovery_run_id,
                    discovery_owner_token=discovery_owner_token,
                    request_fp=request_fp,
                )
            except FirecrawlResultError as exc:
                if ownership_check is not None:
                    ownership_check()
                _settle_provider_receipt(
                    reservation_receipt, "unknown_after_crash", db=db,
                    metadata={"staging_error": type(exc).__name__},
                    discovery_run_id=discovery_run_id,
                    discovery_owner_token=discovery_owner_token,
                )
                raise ProviderSettlementError("Firecrawl result could not be durably staged") from exc
            if ownership_check is not None:
                ownership_check()
            _settle_provider_receipt(
                reservation_receipt, "succeeded", db=db,
                metadata={"result_count": len(jobs)},
                discovery_run_id=discovery_run_id,
                discovery_owner_token=discovery_owner_token,
            )
            # Settlement is now terminal; later persistence failures must leave
            # the staged evidence available for local recovery, not attempt to
            # regress the reservation to an unknown outcome.
            reservation_receipt = None
            recovered = recover_staged_firecrawl_result(
                db,
                operation_key=effective_operation_key,
                execution_id=execution_id,
                query=query,
                discovery_run_id=discovery_run_id,
                discovery_owner_token=discovery_owner_token,
                ownership_check=ownership_check,
                request_fp=request_fp,
            )
            if recovered.blocked or not recovered.found:
                raise ProviderSettlementError("Firecrawl staged result recovery failed")
            return recovered.result, list(recovered.job_ids)
    except ProviderConfigurationError:
        if execution_id is not None:
            _fail_execution(db, execution_id, "Provider configuration error")
        raise
    except FirecrawlOperationConflict:
        raise
    except (RateLimitExceeded, InvalidExecutionTransition):
        raise
    except ProviderSettlementError:
        # The external request already completed, but the receipt is still an
        # unresolved obligation.  Do not turn this into a successful execution
        # and do not call the provider again on replay.
        if execution_id is not None:
            _best_effort_fail_execution(db, execution_id, "Provider reservation settlement unavailable")
        raise
    except DatabaseUnavailable:
        if execution_id is not None:
            _best_effort_fail_execution(db, execution_id, "Quota database unavailable")
        raise
    except ProviderError as e:
        logger.error(f"Provider {provider_name} failed: failure_type={e.__class__.__name__}")
        status_code = getattr(e, "status_code", None)
        if status_code is None and getattr(e, "response", None) is not None:
            status_code = getattr(e.response, "status_code", None)
        if reservation_receipt is not None:
            if ownership_check is not None:
                ownership_check()
            if status_code == 402:
                settlement_status = "payment_required"
            elif (
                getattr(e, "billing_ambiguous", False)
                or status_code is None
                or status_code in (408, 429)
                or status_code >= 500
            ):
                settlement_status = "unknown_after_crash"
            else:
                settlement_status = "failed"
            _settle_provider_receipt(
                reservation_receipt,
                settlement_status,
                db=db,
                metadata={"status_code": status_code, "error_type": type(e).__name__},
                discovery_run_id=discovery_run_id,
                discovery_owner_token=discovery_owner_token,
            )
        if status_code == 402:
            # Payment required is a durable provider latch, not a retryable
            # provider failure.
            if execution_id is not None:
                _fail_execution(db, execution_id, "Payment required")
            raise PaymentRequired(f"Payment required for {prov_key}") from e
        if getattr(e, "billing_ambiguous", False) or status_code is None or status_code in (408, 429) or status_code >= 500:
            if execution_id is not None:
                _fail_execution(db, execution_id, "Provider outcome unknown after crash/transport failure")
            result.failed = 1
            return result, job_ids
        if execution_id is not None:
            _fail_execution(db, execution_id, str(e))
        result.failed = 1
        return result, job_ids
    except Exception as e:
        logger.error(f"Provider {provider_name} failed due to unexpected error: failure_type={e.__class__.__name__}")
        if reservation_receipt is not None:
            if ownership_check is not None:
                ownership_check()
            _settle_provider_receipt(
                reservation_receipt,
                "unknown_after_crash",
                db=db,
                metadata={"error_type": type(e).__name__},
                discovery_run_id=discovery_run_id,
                discovery_owner_token=discovery_owner_token,
            )
        if execution_id is not None:
            _fail_execution(db, execution_id, "Provider outcome unknown after crash/transport failure")
        result.failed = 1
        return result, job_ids

    result.fetched = len(jobs)

    for job in jobs:
        try:
            with db.begin_nested():
                db_job, created = save_job(db, job, execution_id=execution_id)
                # Apply the shared policy to the persisted canonical JobModel.
                # This keeps deduplicated jobs scoped per execution rather than
                # deleting recommendations belonging to another user.
                from app.services.location_policy import posting_matches_retrieval_location
                if not posting_matches_retrieval_location(
                    db_job.location, db_job.remote, getattr(query, "location", None)
                ):
                    logger.warning("Location policy rejected persisted job %s", db_job.id)
                    result.invalid += 1
                    continue
                job_ids.append(db_job.id)
            if created:
                result.created += 1
            else:
                result.duplicates += 1
        except IntegrityError as e:
            if hasattr(e.orig, "sqlstate") and e.orig.sqlstate == "23505":
                result.duplicates += 1
            else:
                logger.warning(f"Failed to persist job {job.source_job_id} due to non-duplicate integrity error")
                result.invalid += 1
        except Exception:
            logger.warning(f"Failed to persist job {job.source_job_id} due to unexpected error")
            result.invalid += 1

    if execution_id is not None:
        _transition_execution(
            db,
            execution_id,
            "started",
            "succeeded",
            {
                "completed_at": datetime.now(timezone.utc),
                "jobs_fetched": result.fetched,
                "jobs_created": result.created,
                "jobs_duplicates": result.duplicates,
                "jobs_invalid": result.invalid,
            },
        )
    db.commit()
    return result, job_ids
