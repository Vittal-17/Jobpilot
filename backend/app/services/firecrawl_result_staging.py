"""Bounded, tamper-evident staging and replay for paid Firecrawl searches."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.job import Job
from app.db.repository.job_repository import save_job

MAX_JOBS = 100
MAX_PAYLOAD_BYTES = 1024 * 1024
_FIELD_LIMITS = {
    "title": 255, "company": 255, "source": 100, "source_job_id": 255,
    "location": 255, "employment_type": 100, "currency": 10, "url": 2048,
    "description": 100_000,
}
_JOB_FIELDS = frozenset({
    "title", "company", "source", "source_job_id", "discovered_at", "location",
    "remote", "employment_type", "description", "description_is_snippet", "salary_min",
    "salary_max", "currency", "url", "published_at", "match_score",
})


class FirecrawlResultError(ValueError):
    """The result is not safe to persist or replay."""


class FirecrawlOperationConflict(ValueError):
    """An idempotency key was reused for a different request."""


@dataclass(frozen=True)
class StagedResult:
    payload: dict[str, Any]
    sha256: str
    byte_count: int
    job_count: int


@dataclass(frozen=True)
class RecoveryResult:
    found: bool
    blocked: bool = False
    result: Any = None
    job_ids: tuple[int, ...] = ()


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _validate_job_dict(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict) or not set(item).issubset(_JOB_FIELDS):
        raise FirecrawlResultError("staged job contains non-whitelisted fields")
    for field, limit in _FIELD_LIMITS.items():
        value = item.get(field)
        if value is not None and len(str(value)) > limit:
            raise FirecrawlResultError(f"staged job field {field} exceeds bound")
    try:
        job = Job.model_validate(item)
    except Exception as exc:
        raise FirecrawlResultError("staged job is not a valid canonical Job") from exc
    return job.model_dump(mode="json", exclude_none=False)


def serialize_jobs(jobs: Iterable[Job | dict[str, Any]]) -> StagedResult:
    normalized = [_validate_job_dict(job.model_dump(mode="json", exclude_none=False) if isinstance(job, Job) else job) for job in jobs]
    if len(normalized) > MAX_JOBS:
        raise FirecrawlResultError(f"Firecrawl result exceeds {MAX_JOBS} jobs")
    payload = {"version": 1, "jobs": normalized}
    encoded = _canonical_json(payload)
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise FirecrawlResultError("Firecrawl result exceeds the 1 MiB staging bound")
    return StagedResult(payload, hashlib.sha256(encoded).hexdigest(), len(encoded), len(normalized))


def deserialize_jobs(payload: Any, *, sha256: str, byte_count: int, job_count: int) -> list[Job]:
    if not isinstance(payload, dict) or set(payload) != {"version", "jobs"} or payload.get("version") != 1:
        raise FirecrawlResultError("invalid staged payload envelope")
    jobs_raw = payload.get("jobs")
    if not isinstance(jobs_raw, list) or len(jobs_raw) > MAX_JOBS or len(jobs_raw) != job_count:
        raise FirecrawlResultError("invalid staged job count")
    encoded = _canonical_json(payload)
    actual_sha = hashlib.sha256(encoded).hexdigest()
    if len(encoded) != byte_count or actual_sha != sha256 or len(encoded) > MAX_PAYLOAD_BYTES:
        raise FirecrawlResultError("staged payload integrity or size check failed")
    return [Job.model_validate(_validate_job_dict(item)) for item in jobs_raw]


def request_fingerprint(*, provider_name: str, query: Any, execution_id: int | None) -> str:
    """Hash every paid request input that can change the provider result."""
    if hasattr(query, "model_dump"):
        query_payload = query.model_dump(mode="json", exclude_none=False)
    elif isinstance(query, dict):
        query_payload = dict(query)
    else:
        raise FirecrawlResultError("request query is not canonical")
    payload = {
        "provider": str(provider_name),
        "execution_id": execution_id,
        "query": query_payload,
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _validate_reservation(
    db: Session,
    *,
    operation_key: str,
    operation: str,
    cost_units: int,
    execution_id: int | None,
    reservation_id: str | None,
    request_fp: str | None,
    discovery_run_id: str | None = None,
    discovery_owner_token: str | None = None,
    lock: bool = True,
):
    if not reservation_id:
        raise FirecrawlResultError("staged result requires a reservation receipt")
    if discovery_run_id is not None:
        parent = db.execute(text("""
            SELECT status, owner_token, owner_expires_at
            FROM firecrawl_discovery_runs WHERE run_id=:run_id
            {lock_clause}
        """.format(lock_clause="FOR UPDATE" if lock else "")), {
            "run_id": discovery_run_id,
        }).mappings().first()
        if (
            parent is None
            or parent["status"] != "running"
            or parent["owner_token"] != discovery_owner_token
            or parent["owner_expires_at"] is None
            or parent["owner_expires_at"] <= datetime.now(timezone.utc)
        ):
            raise FirecrawlResultError("discovery ownership lease is not valid")
        execution = db.execute(text("""
            SELECT cycle_id, status, ownership_token, ownership_expires_at
            FROM search_execution WHERE id=:execution_id
            {lock_clause}
        """.format(lock_clause="FOR UPDATE" if lock else "")), {
            "execution_id": execution_id,
        }).mappings().first()
        if (
            execution is None
            or execution["cycle_id"] != discovery_run_id
            or execution["status"] not in {"selected", "started", "succeeded"}
            or (
                execution["status"] in {"selected", "started"}
                and (
                    execution["ownership_token"] != discovery_owner_token
                    or execution["ownership_expires_at"] is None
                    or execution["ownership_expires_at"] <= datetime.now(timezone.utc)
                )
            )
        ):
            raise FirecrawlResultError("discovery execution ownership lease is not valid")
    row = db.execute(text("""
        SELECT id, operation_key, provider_name, cost_units, execution_id,
               status, request_fingerprint
        FROM provider_reservations WHERE id=:reservation_id
        {lock_clause}
    """.format(lock_clause="FOR UPDATE" if lock else "")), {"reservation_id": reservation_id}).mappings().first()
    if not row:
        raise FirecrawlResultError("staged result reservation receipt is missing")
    if (
        row["operation_key"] != operation_key
        or row["provider_name"] not in {"firecrawl", "firecrawl_monthly"}
        or int(row["cost_units"]) != int(cost_units)
        or row["execution_id"] != execution_id
        or (request_fp is not None and row["request_fingerprint"] not in {None, request_fp})
    ):
        raise FirecrawlResultError("staged result reservation does not match the operation")
    if row["status"] not in {"reserved", "in_flight", "succeeded"}:
        raise FirecrawlResultError("staged result reservation is not recoverable")
    return row


def stage_firecrawl_result(
    db: Session, *, operation_key: str, operation: str, cost_units: int,
    execution_id: int | None, reservation_id: str, jobs: Iterable[Job],
    request_fp: str | None = None,
    discovery_run_id: str | None = None,
    discovery_owner_token: str | None = None,
) -> StagedResult:
    staged = serialize_jobs(jobs)
    reservation = _validate_reservation(
        db, operation_key=operation_key, operation=operation, cost_units=cost_units,
        execution_id=execution_id, reservation_id=reservation_id, request_fp=request_fp,
        discovery_run_id=discovery_run_id,
        discovery_owner_token=discovery_owner_token,
        lock=True,
    )
    now = datetime.now(timezone.utc)
    existing = db.execute(text("""
        SELECT reservation_id, operation_key, request_fingerprint, result_state,
               staged_payload_sha256
        FROM firecrawl_operations WHERE operation_key=:key FOR UPDATE
    """), {"key": operation_key}).mappings().first()
    if existing:
        if (
            existing["reservation_id"] != reservation_id
            or existing["operation_key"] != operation_key
            or (request_fp is not None and existing["request_fingerprint"] not in {None, request_fp})
        ):
            raise FirecrawlOperationConflict("operation key is bound to different request or reservation")
    row = db.execute(text("""
        INSERT INTO firecrawl_operations
            (operation, cost_units, status, operation_key, request_fingerprint, execution_id, reservation_id,
             result_state, staged_payload, staged_payload_sha256, staged_payload_bytes,
             staged_job_count, staged_at, created_at)
        VALUES (:operation, :cost, 'in_flight', :key, :request_fingerprint, :execution, :reservation,
                 'staged', CAST(:payload AS jsonb), :sha, :bytes, :count, :staged_at, :created_at)
        ON CONFLICT (operation_key) DO UPDATE SET
            execution_id=COALESCE(firecrawl_operations.execution_id, EXCLUDED.execution_id),
            reservation_id=COALESCE(firecrawl_operations.reservation_id, EXCLUDED.reservation_id),
            request_fingerprint=COALESCE(firecrawl_operations.request_fingerprint, EXCLUDED.request_fingerprint),
            result_state=CASE WHEN firecrawl_operations.result_state='none' AND firecrawl_operations.status NOT IN ('success','succeeded','failed','unknown_after_crash','payment_required') THEN EXCLUDED.result_state ELSE firecrawl_operations.result_state END,
            staged_payload=CASE WHEN firecrawl_operations.result_state='none' AND firecrawl_operations.status NOT IN ('success','succeeded','failed','unknown_after_crash','payment_required') THEN EXCLUDED.staged_payload ELSE firecrawl_operations.staged_payload END,
            staged_payload_sha256=CASE WHEN firecrawl_operations.result_state='none' AND firecrawl_operations.status NOT IN ('success','succeeded','failed','unknown_after_crash','payment_required') THEN EXCLUDED.staged_payload_sha256 ELSE firecrawl_operations.staged_payload_sha256 END,
            staged_payload_bytes=CASE WHEN firecrawl_operations.result_state='none' AND firecrawl_operations.status NOT IN ('success','succeeded','failed','unknown_after_crash','payment_required') THEN EXCLUDED.staged_payload_bytes ELSE firecrawl_operations.staged_payload_bytes END,
            staged_job_count=CASE WHEN firecrawl_operations.result_state='none' AND firecrawl_operations.status NOT IN ('success','succeeded','failed','unknown_after_crash','payment_required') THEN EXCLUDED.staged_job_count ELSE firecrawl_operations.staged_job_count END,
            staged_at=CASE WHEN firecrawl_operations.result_state='none' AND firecrawl_operations.status NOT IN ('success','succeeded','failed','unknown_after_crash','payment_required') THEN EXCLUDED.staged_at ELSE firecrawl_operations.staged_at END
        RETURNING result_state, staged_payload_sha256, staged_payload_bytes, staged_job_count
    """), {
        "operation": operation, "cost": cost_units, "key": operation_key,
        "request_fingerprint": request_fp,
        "execution": execution_id, "reservation": reservation_id,
        "payload": json.dumps(staged.payload, ensure_ascii=False), "sha": staged.sha256,
        "bytes": staged.byte_count, "count": staged.job_count, "staged_at": now, "created_at": now,
    }).mappings().first()
    if not row or row["result_state"] not in {"staged", "consumed"} or row["staged_payload_sha256"] != staged.sha256:
        raise FirecrawlResultError("operation key already has different or terminal result")
    db.commit()  # stage durability precedes reservation settlement
    return staged


def _operation_row(db: Session, operation_key: str):
    return db.execute(text("""
        SELECT * FROM firecrawl_operations WHERE operation_key=:key
    """), {"key": operation_key}).mappings().first()


def recover_staged_firecrawl_result(
    db: Session, *, operation_key: str, execution_id: int | None = None,
    query: Any = None, request_fp: str | None = None,
    discovery_run_id: str | None = None,
    discovery_owner_token: str | None = None,
    ownership_check: Any = None,
) -> RecoveryResult:
    """Replay a staged operation without crossing the provider boundary."""
    row = _operation_row(db, operation_key)
    if not row:
        reservation = db.execute(text(
            "SELECT request_fingerprint FROM provider_reservations WHERE operation_key=:key"
        ), {"key": operation_key}).scalar()
        if request_fp is not None and reservation is not None and reservation != request_fp:
            raise FirecrawlOperationConflict("operation key is bound to a different request")
        return RecoveryResult(found=False)
    if request_fp is not None and row["request_fingerprint"] != request_fp:
        raise FirecrawlOperationConflict("operation key is bound to a different request")
    if row["result_state"] not in {"staged", "consumed"} or not row["staged_payload"]:
        db.rollback()
        return RecoveryResult(found=True, blocked=True)
    try:
        jobs = deserialize_jobs(row["staged_payload"], sha256=row["staged_payload_sha256"], byte_count=row["staged_payload_bytes"], job_count=row["staged_job_count"])
    except FirecrawlResultError:
        db.rollback()
        raise

    try:
        reservation = _validate_reservation(
            db,
            operation_key=operation_key,
            operation=str(row["operation"]),
            cost_units=int(row["cost_units"]),
            execution_id=execution_id,
            reservation_id=row["reservation_id"],
            request_fp=request_fp or row["request_fingerprint"],
            discovery_run_id=discovery_run_id,
            discovery_owner_token=discovery_owner_token,
            lock=False,
        )
    except FirecrawlResultError:
        db.rollback()
        return RecoveryResult(found=True, blocked=True)
    if reservation["status"] in {"reserved", "in_flight"}:
        from app.services.firecrawl_quota import get_reservation_receipt, settle_provider_reservation
        if ownership_check is not None:
            ownership_check()
        receipt = get_reservation_receipt(db, row["reservation_id"])
        settled = settle_provider_reservation(
            receipt,
            "succeeded",
            db=db,
            metadata={"result_count": len(jobs), "staged": True},
            discovery_run_id=discovery_run_id,
            discovery_owner_token=discovery_owner_token,
        ) if receipt else None
        if settled is None or settled.status != "succeeded":
            db.rollback()
            return RecoveryResult(found=True, blocked=True)

    if ownership_check is not None:
        ownership_check()
    from app.services.location_policy import posting_matches_retrieval_location
    from app.schemas.job_search import IngestionResult
    result = IngestionResult(provider="firecrawl", fetched=len(jobs))
    ids: list[int] = []
    for job in jobs:
        with db.begin_nested():
            db_job, created = save_job(db, job, execution_id=execution_id)
            if query is not None and not posting_matches_retrieval_location(db_job.location, db_job.remote, getattr(query, "location", None)):
                result.invalid += 1
                continue
            ids.append(db_job.id)
            if created:
                result.created += 1
            else:
                result.duplicates += 1
    if execution_id is not None:
        if ownership_check is not None:
            ownership_check()
        updated = db.execute(text("""
            UPDATE search_execution SET status='succeeded', completed_at=clock_timestamp(),
                jobs_fetched=:fetched, jobs_created=:created, jobs_duplicates=:duplicates,
                jobs_invalid=:invalid
            WHERE id=:execution_id AND status IN ('started', 'failed')
        """), {"execution_id": execution_id, "fetched": result.fetched, "created": result.created,
               "duplicates": result.duplicates, "invalid": result.invalid})
        if updated.rowcount == 0:
            existing = db.execute(text("SELECT status, jobs_fetched, jobs_created, jobs_duplicates, jobs_invalid FROM search_execution WHERE id=:id"), {"id": execution_id}).mappings().first()
            if existing and existing["status"] != "succeeded":
                db.rollback()
                raise RuntimeError("execution is not in a recoverable state")
    db.commit()
    db.execute(text("""
        UPDATE firecrawl_operations SET result_state='consumed', consumed_at=COALESCE(consumed_at, clock_timestamp())
        WHERE operation_key=:key AND result_state='staged'
    """), {"key": operation_key})
    db.commit()
    return RecoveryResult(found=True, result=result, job_ids=tuple(ids))


# Small aliases keep the helper usable by callers that describe the operation
# as "result" rather than "staged result" without introducing a second flow.
serialize_firecrawl_jobs = serialize_jobs
deserialize_firecrawl_jobs = deserialize_jobs
stage_result = stage_firecrawl_result
recover_result = recover_staged_firecrawl_result
