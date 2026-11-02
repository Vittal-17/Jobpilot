"""Firecrawl budget state and provider reservation settlement.

Reservations are deliberately separate from the caller's transaction.  A
reservation is committed before an external request is made and is settled by
receipt afterwards.  This makes a process crash an accounted-for
``unknown_after_crash`` outcome instead of an ordinary retry.
                """

from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.execution_outcome import FirecrawlBudgetModel, ProviderReservationModel

logger = logging.getLogger(__name__)

RESERVATION_STATUSES = frozenset(
    {"reserved", "in_flight", "succeeded", "failed", "payment_required", "unknown_after_crash"}
)
TERMINAL_RESERVATION_STATUSES = frozenset(
    {"succeeded", "failed", "payment_required", "unknown_after_crash"}
)
DISCOVERY_STALE_SECONDS = 15 * 60
DISCOVERY_LEASE_SECONDS = DISCOVERY_STALE_SECONDS
DIRECT_RESERVATION_STALE_SECONDS = 15 * 60


class DiscoveryOwnershipLost(RuntimeError):
    """The worker no longer owns the durable discovery lease."""


def assert_discovery_ownership(
    db: Session,
    *,
    run_id: str,
    owner_token: str,
    execution_id: int | None = None,
) -> None:
    """Fail closed unless the parent and optional child lease are current."""
    parent = db.execute(text("""
        SELECT owner_token, owner_expires_at
        FROM firecrawl_discovery_runs
        WHERE run_id=:run_id AND status='running'
          AND owner_token=:owner_token
          AND owner_expires_at > clock_timestamp()
    """), {"run_id": run_id, "owner_token": owner_token}).first()
    if parent is None:
        raise DiscoveryOwnershipLost(f"Discovery run {run_id} is no longer owned")
    if execution_id is not None:
        child = db.execute(text("""
            SELECT ownership_token, ownership_expires_at, status
            FROM search_execution
            WHERE id=:execution_id AND cycle_id=:run_id
        """), {"execution_id": execution_id, "run_id": run_id}).mappings().first()
        if child is None or (
            child["status"] in {"selected", "started"}
            and (
                child["ownership_token"] != owner_token
                or child["ownership_expires_at"] is None
                or child["ownership_expires_at"] <= datetime.now(timezone.utc)
            )
        ):
            raise DiscoveryOwnershipLost(
                f"Discovery execution {execution_id} is no longer owned"
            )


def renew_discovery_ownership(
    db: Session,
    *,
    run_id: str,
    owner_token: str,
    execution_id: int | None = None,
) -> datetime:
    """Renew the parent lease and, when active, its child query lease."""
    renewed = db.execute(text("""
        UPDATE firecrawl_discovery_runs
        SET owner_expires_at = clock_timestamp() + (:seconds || ' seconds')::INTERVAL
        WHERE run_id=:run_id AND status='running'
          AND owner_token=:owner_token
          AND owner_expires_at > clock_timestamp()
        RETURNING owner_expires_at
    """), {
        "seconds": DISCOVERY_LEASE_SECONDS,
        "run_id": run_id,
        "owner_token": owner_token,
    }).scalar_one_or_none()
    if renewed is None:
        db.rollback()
        raise DiscoveryOwnershipLost(f"Discovery run {run_id} lease expired")
    if execution_id is not None:
        child = db.execute(text("""
            UPDATE search_execution
            SET ownership_expires_at=:expires_at
            WHERE id=:execution_id AND cycle_id=:run_id
              AND status IN ('selected', 'started')
              AND ownership_token=:owner_token
            RETURNING id
        """), {
            "expires_at": renewed,
            "execution_id": execution_id,
            "run_id": run_id,
            "owner_token": owner_token,
        }).scalar_one_or_none()
        if child is None:
            status = db.execute(text(
                "SELECT status FROM search_execution WHERE id=:execution_id AND cycle_id=:run_id"
            ), {"execution_id": execution_id, "run_id": run_id}).scalar()
            if status in {"selected", "started"}:
                db.rollback()
                raise DiscoveryOwnershipLost(
                    f"Discovery execution {execution_id} lease expired"
                )
    db.commit()
    return renewed


@dataclass(frozen=True)
class ProviderReservationReceipt:
    """Durable authorization returned before a paid provider call."""

    reservation_id: str
    operation_key: str
    provider_name: str
    cost_units: int
    status: str = "reserved"
    usage_date: date | None = None
    execution_id: int | None = None
    job_id: int | None = None
    lease_token: str | None = None

    # ``id`` keeps the receipt convenient for callers that previously dealt
    # with a reservation model rather than a receipt object.
    @property
    def id(self) -> str:
        return self.reservation_id

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.reservation_id,
            "reservation_id": self.reservation_id,
            "operation_key": self.operation_key,
            "provider_name": self.provider_name,
            "cost_units": self.cost_units,
            "status": self.status,
            "usage_date": self.usage_date,
            "execution_id": self.execution_id,
            "job_id": self.job_id,
            "lease_token": self.lease_token,
        }

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.as_dict().get(key, default)

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "ProviderReservationReceipt":
        return cls(
            reservation_id=str(row["id"]),
            operation_key=str(row["operation_key"]),
            provider_name=str(row["provider_name"]),
            cost_units=int(row["cost_units"]),
            status=str(row["status"]),
            usage_date=row.get("usage_date"),
            execution_id=row.get("execution_id"),
            job_id=row.get("job_id"),
            lease_token=row.get("lease_token"),
        )


def reserve_provider_request(*args, **kwargs):
    """Canonical provider-reservation import path.

    The quota implementation lives in ``ingestion`` for compatibility with
    the existing provider policy service; this lazy delegate avoids a module
    import cycle while giving paid callers one obvious primitive.
    """
    from app.services.ingestion import reserve_provider_request as _reserve

    return _reserve(*args, **kwargs)


def calculate_search_credits(limit: int = 10) -> int:
    """Firecrawl search costs two credits per ten bounded results."""

    try:
        bounded = max(1, int(limit))
    except (TypeError, ValueError):
        # The provider client will fail closed for a non-integer limit.  Keep
        # this legacy helper deterministic for existing callers.
        bounded = 1
    return max(2, math.ceil(bounded / 10) * 2)


def independent_bind(db: Session | None = None):
    """Return an Engine for an independent transaction when available."""

    if db is None:
        from app.db.database import engine

        return engine
    bind = db.get_bind()
    # SQLAlchemy sessions expose an Engine directly.  Keep lightweight test
    # session doubles intact instead of following MagicMock's dynamic
    # ``engine`` attribute into an unrelated object.
    from sqlalchemy.engine import Engine

    return bind if isinstance(bind, Engine) else db


def lock_firecrawl_budget(db: Session) -> None:
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('provider_quota'),hashtext('firecrawl'))")
    )


def policy_values() -> tuple[int, int, int]:
    budget = int(settings.firecrawl_monthly_budget)
    reserve = int(settings.firecrawl_reserved_credits)
    cap = int(settings.firecrawl_monthly_automation_cap)
    if min(budget, reserve, cap) < 0:
        raise ValueError("Invalid Firecrawl budget policy")
    if budget < reserve:
        raise ValueError("Invalid Firecrawl budget policy: protected reserve exceeds account budget")
    return budget, min(cap, budget - reserve), reserve


def ensure_firecrawl_budget(db: Session, today: date) -> dict[str, Any]:
    budget, cap, reserve = policy_values()
    month = today.replace(day=1)
    db.execute(
        insert(FirecrawlBudgetModel)
        .values(
            month=month,
            account_budget=budget,
            automation_cap=cap,
            protected_reserve=reserve,
            payment_required=False,
        )
        .on_conflict_do_nothing(index_elements=[FirecrawlBudgetModel.month])
    )
    row = dict(
        db.execute(
            select(FirecrawlBudgetModel.__table__).where(FirecrawlBudgetModel.month == month)
        )
        .mappings()
        .one()
    )
    # Preserve the payment latch written by older versions which only had an
    # operational event and no budget-state row.
    legacy = db.execute(
        text(
            "SELECT MIN(created_at) FROM firecrawl_operations "
            "WHERE status='payment_required' AND created_at>=:m "
            "AND created_at<:m+INTERVAL '1 month'"
        ),
        {"m": month},
    ).scalar()
    if legacy and not row["payment_required"]:
        db.execute(
            update(FirecrawlBudgetModel)
            .where(FirecrawlBudgetModel.month == month)
            .values(payment_required=True, exhausted_at=legacy)
        )
        row["payment_required"] = True
    return row


def policy_matches(row: Mapping[str, Any]) -> bool:
    return (
        row["account_budget"],
        row["automation_cap"],
        row["protected_reserve"],
    ) == policy_values()


def latch_firecrawl_payment_required(
    db: Session | None = None,
    reference_time: datetime | None = None,
) -> None:
    """Durably stop automated Firecrawl calls for the current month."""

    today = (reference_time or datetime.now(timezone.utc)).date()
    with Session(independent_bind(db)) as session:
        lock_firecrawl_budget(session)
        row = ensure_firecrawl_budget(session, today)
        session.execute(
            update(FirecrawlBudgetModel)
            .where(FirecrawlBudgetModel.month == row["month"])
            .values(payment_required=True, exhausted_at=datetime.now(timezone.utc))
        )
        # Ensure a durable firecrawl_operations row with status='payment_required' exists exactly once
        # Note: operation must satisfy chk_firecrawl_ops_operation ('discovery', 'enrichment')
        # cost_units=1 is used for telemetry consistency with paid enrichment requests
        op_key = f"sync_402:{row['month']}"
        session.execute(
            text(
                "INSERT INTO firecrawl_operations "
                "(operation, cost_units, status, operation_key, created_at) "
                "VALUES ('enrichment', 1, 'payment_required', :op_key, CURRENT_TIMESTAMP) "
                "ON CONFLICT (operation_key) DO NOTHING"
            ),
            {"op_key": op_key},
        )
        session.commit()


def _operation_status(status: str) -> str:
    """Use the legacy ``success`` spelling in operation telemetry.

    Provider reservations use the canonical ``succeeded`` spelling.  Existing
    dashboards and clients consume ``firecrawl_operations.status=success``;
    accepting both keeps that read API backwards compatible.
    """

    return "success" if status == "succeeded" else status


def record_firecrawl_operation(
    operation: str,
    cost_units: int,
    status: str,
    *,
    db: Session | None = None,
    execution_id: int | None = None,
    job_id: int | None = None,
    reservation_id: str | None = None,
    operation_key: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Record best-effort telemetry without committing the caller session."""

    if status == "payment_required":
        latch_firecrawl_payment_required(db)
    from app.db.database import SessionLocal

    try:
        with SessionLocal() as session:
            # Discovery still has a legacy telemetry call after ingestion.
            # When an execution already owns a reservation, fold that call
            # onto the receipt's operation key instead of creating a second
            # operation row for the same paid request.
            if execution_id is not None and operation_key is not None and reservation_id is None:
                receipt_row = session.execute(
                    text(
                        "SELECT id, operation_key FROM provider_reservations "
                        "WHERE execution_id=:execution_id "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"execution_id": execution_id},
                ).mappings().first()
                if isinstance(receipt_row, Mapping):
                    reservation_id = receipt_row["id"]
                    operation_key = receipt_row["operation_key"]
            session.execute(
                text(
                        "INSERT INTO firecrawl_operations "
                        "(operation,cost_units,status,created_at,execution_id,job_id,"
                        "reservation_id,operation_key,request_fingerprint,metadata_json) "
                        "VALUES (:o,:c,:st,clock_timestamp(),:e,:j,:r,:k,:f,CAST(:m AS jsonb)) "
                     "ON CONFLICT (operation_key) DO UPDATE SET "
                     "status=EXCLUDED.status, "
                     "execution_id=COALESCE(firecrawl_operations.execution_id, EXCLUDED.execution_id), "
                     "job_id=COALESCE(firecrawl_operations.job_id, EXCLUDED.job_id), "
                     "reservation_id=COALESCE(firecrawl_operations.reservation_id, EXCLUDED.reservation_id), "
                     "metadata_json=COALESCE(EXCLUDED.metadata_json, firecrawl_operations.metadata_json) "
                    "WHERE (firecrawl_operations.status NOT IN ('success','succeeded','failed','unknown_after_crash','payment_required') "
                    "AND NOT (firecrawl_operations.status='in_flight' AND EXCLUDED.status='reserved')) "
                ),
                {
                    "o": operation,
                    "c": cost_units,
                    "st": _operation_status(status),
                    "e": execution_id,
                    "j": job_id,
                     "r": reservation_id,
                     "k": operation_key,
                     "f": None,
                     "m": json.dumps(metadata) if metadata is not None else None,
                },
            )
            session.commit()
    except Exception:
        # Telemetry cannot be allowed to corrupt or abort the caller's flow.
        logger.exception("Firecrawl telemetry write failed")


def _receipt_row(session: Session, reservation_id: str) -> Mapping[str, Any] | None:
    row = session.execute(
        select(ProviderReservationModel.__table__).where(
            ProviderReservationModel.id == reservation_id
        )
    ).mappings().first()
    return row if isinstance(row, Mapping) else None


def get_reservation_receipt(
    db: Session,
    reservation_id: str,
) -> ProviderReservationReceipt | None:
    with Session(independent_bind(db)) as session:
        row = _receipt_row(session, reservation_id)
        return ProviderReservationReceipt.from_mapping(row) if row else None


def mark_provider_request_in_flight(
    receipt: ProviderReservationReceipt,
    *,
    db: Session | None = None,
    discovery_run_id: str | None = None,
    discovery_owner_token: str | None = None,
) -> ProviderReservationReceipt | None:
    # Only the worker that changes reserved -> in_flight receives permission
    # to call the provider.  An already in-flight or terminal receipt is NOT
    # an authorization to replay an uncertain external request.
    return settle_provider_reservation(
        receipt,
        "in_flight",
        db=db,
        require_transition=True,
        discovery_run_id=discovery_run_id,
        discovery_owner_token=discovery_owner_token,
    )


def cancel_reserved_provider_request(
    receipt,
    *,
    db: Session,
    reason: str,
    discovery_run_id: str | None = None,
    discovery_owner_token: str | None = None,
):
    """Cancel only an atomically verified never-dispatched reservation."""
    return settle_provider_reservation(
        receipt, "failed", db=db, allow_reserved_terminal=True,
        reserved_only=True, metadata={"external_call": False, "reason": reason},
        discovery_run_id=discovery_run_id,
        discovery_owner_token=discovery_owner_token,
    )


def reconcile_enrichment_reservations(db: Session, job_id: int | None = None) -> int:
    """Reconcile abandoned unstaged obligations in the caller transaction.

    Read locators first, then lock parent ownership before its receipts, just
    like dispatch/staging/settlement. Age alone is not evidence of abandonment.
    Usage is deliberately retained. Exact staged content remains recoverable.
    """
    filter_sql = "" if job_id is None else " AND job_id=:jid"
    job_ids = db.execute(text(f"""
        SELECT DISTINCT job_id FROM provider_reservations
        WHERE job_id IS NOT NULL AND lease_token IS NOT NULL
          AND status IN ('reserved','in_flight')
          {filter_sql} ORDER BY job_id
    """), {"jid": job_id} if job_id is not None else {}).scalars().all()
    reconciled = 0
    for jid in job_ids:
        db.execute(text("SELECT job_id FROM job_enrichments WHERE job_id=:jid FOR UPDATE"), {"jid": jid})
        rows = db.execute(text("""
            UPDATE provider_reservations r
            SET status='unknown_after_crash',
                billing_metadata=COALESCE(billing_metadata, '{}'::jsonb)
                    || '{"reason":"stale_reserved_reconciled"}'::jsonb
            WHERE job_id=:jid AND lease_token IS NOT NULL
              AND status IN ('reserved','in_flight')
              AND NOT EXISTS (
                  SELECT 1 FROM job_enrichments e WHERE e.job_id=:jid
                  AND ((e.status='in_progress' AND e.lease_token=r.lease_token
                        AND e.lease_expires_at>clock_timestamp())
                       OR (e.staged_reservation_id=r.id AND NULLIF(e.staged_content, '') IS NOT NULL)))
            RETURNING r.*
        """), {"jid": jid}).mappings().all()
        for row in rows:
            if row["provider_name"] in {"firecrawl", "firecrawl_monthly"}:
                db.execute(text("""
                    INSERT INTO firecrawl_operations
                        (operation,cost_units,status,created_at,execution_id,job_id,
                         reservation_id,operation_key,metadata_json)
                    VALUES (:o,:c,'unknown_after_crash',clock_timestamp(),:e,:j,:r,:k,CAST(:m AS jsonb))
                    ON CONFLICT (operation_key) DO UPDATE
                    SET status=EXCLUDED.status, metadata_json=EXCLUDED.metadata_json
                        ,execution_id=COALESCE(firecrawl_operations.execution_id, EXCLUDED.execution_id)
                        ,job_id=COALESCE(firecrawl_operations.job_id, EXCLUDED.job_id)
                        ,reservation_id=COALESCE(firecrawl_operations.reservation_id, EXCLUDED.reservation_id)
                    WHERE firecrawl_operations.status NOT IN
                        ('success','succeeded','failed','unknown_after_crash','payment_required')
                """), {"o": "discovery" if row["provider_name"] == "firecrawl" else "enrichment",
                       "c": row["cost_units"], "e": row["execution_id"], "j": jid,
                       "r": row["id"], "k": row["operation_key"], "m": json.dumps(row["billing_metadata"])})
        reconciled += len(rows)
    return reconciled


def reconcile_stale_enrichment_reservations(db: Session, job_id: int | None = None) -> int:
    """Worker lifecycle boundary, independent of pending caller-owned writes."""
    with Session(independent_bind(db)) as session:
        count = reconcile_enrichment_reservations(session, job_id)
        session.commit()
        return count


def reconcile_discovery_reservations(
    db: Session,
    *,
    run_id: str | None = None,
    cycle_id: str | None = None,
    execution_id: int | None = None,
) -> int:
    """Reconcile abandoned discovery receipts in one ownership transaction.

    Discovery reservations have no ``job_id`` lease.  Their durable identity
    is the search execution's ``execution_id`` and its ``cycle_id`` (the
    discovery run id).  Lock the run, executions, and then reservations in
    that order so a stale-run takeover cannot race a worker that is still
    publishing its result.  Usage is never refunded and terminal operation
    evidence is never overwritten.
    """
    if run_id is None and cycle_id is None and execution_id is None:
        return 0

    run_is_stale = False
    run_owner_token: str | None = None
    if run_id is not None:
        run_row = db.execute(
            text(
                "SELECT run_id, status, owner_token, owner_expires_at "
                "FROM firecrawl_discovery_runs WHERE run_id=:run_id FOR UPDATE"
            ),
            {"run_id": run_id},
        ).mappings().first()
        if run_row is not None:
            run_owner_token = run_row["owner_token"]
            run_status = str(run_row["status"] or "")
            run_is_stale = run_status == "unknown" or (
                run_status == "running"
                and (
                    run_row["owner_expires_at"] is None
                    or run_row["owner_expires_at"] <= datetime.now(timezone.utc)
                )
            )
            if run_status not in {"running", "unknown"}:
                return 0
        else:
            return 0
        cycle_id = run_id

    execution_params: dict[str, Any] = {}
    predicates = ["provider_name = 'firecrawl'"]
    if cycle_id is not None:
        predicates.append("cycle_id = :cycle_id")
        execution_params["cycle_id"] = cycle_id
    if execution_id is not None:
        predicates.append("id = :execution_id")
        execution_params["execution_id"] = execution_id

    executions = db.execute(
        text(
            "SELECT id, status, ownership_token, ownership_expires_at "
            "FROM search_execution WHERE " + " AND ".join(predicates) +
            " ORDER BY id FOR UPDATE"
        ),
        execution_params,
    ).mappings().all()

    reconciled = 0
    for execution in executions:
        # A child lease is independent evidence of a live worker.  An old
        # parent timestamp is never enough to invalidate it.
        live = db.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM search_execution "
                "WHERE id=:execution_id AND status IN ('selected','started') "
                "AND ownership_token IS NOT NULL "
                "AND ownership_expires_at > clock_timestamp())"
            ),
            {"execution_id": execution["id"]},
        ).scalar()
        if live:
            continue
        if not run_is_stale:
            continue

        reservations = db.execute(
            text(
                "SELECT * FROM provider_reservations "
                "WHERE execution_id=:execution_id AND job_id IS NULL "
                "AND status IN ('reserved','in_flight') FOR UPDATE"
            ),
            {"execution_id": execution["id"]},
        ).mappings().all()
        for reservation in reservations:
            operation = db.execute(text(
                "SELECT result_state FROM firecrawl_operations "
                "WHERE operation_key=:operation_key FOR UPDATE"
            ), {"operation_key": reservation["operation_key"]}).mappings().first()
            if operation and operation["result_state"] in {"staged", "consumed"}:
                continue
            current_parent = db.execute(text("""
                SELECT status, owner_token, owner_expires_at
                FROM firecrawl_discovery_runs WHERE run_id=:run_id
                FOR UPDATE
            """), {"run_id": cycle_id}).mappings().first()
            if current_parent and current_parent["status"] == "running" and (
                current_parent["owner_token"] is not None
                and current_parent["owner_expires_at"] is not None
                and current_parent["owner_expires_at"] > datetime.now(timezone.utc)
            ):
                continue
            current_child = db.execute(text("""
                SELECT status, ownership_token, ownership_expires_at
                FROM search_execution WHERE id=:execution_id FOR UPDATE
            """), {"execution_id": execution["id"]}).mappings().first()
            if current_child and current_child["status"] in {"selected", "started"} and (
                current_child["ownership_token"] is not None
                and current_child["ownership_expires_at"] is not None
                and current_child["ownership_expires_at"] > datetime.now(timezone.utc)
            ):
                continue
            metadata = {
                "reason": "stale_discovery_reconciled",
                "run_id": cycle_id,
                "execution_id": execution["id"],
            }
            db.execute(
                text(
                    "UPDATE provider_reservations SET status='unknown_after_crash', "
                    "billing_metadata=COALESCE(billing_metadata, '{}'::jsonb) "
                    "|| CAST(:metadata AS jsonb) "
                    "WHERE id=:reservation_id AND status IN ('reserved','in_flight')"
                ),
                {
                    "reservation_id": reservation["id"],
                    "metadata": json.dumps(metadata),
                },
            )
            db.execute(
                text(
                    "INSERT INTO firecrawl_operations "
                    "(operation,cost_units,status,created_at,execution_id,job_id,"
                    "reservation_id,operation_key,request_fingerprint,metadata_json) "
                    "VALUES ('discovery',:cost,'unknown_after_crash',clock_timestamp(),"
                    ":execution_id,NULL,:reservation_id,:operation_key,:request_fingerprint,CAST(:metadata AS jsonb)) "
                    "ON CONFLICT (operation_key) DO UPDATE SET "
                    "status=CASE WHEN firecrawl_operations.status IN "
                    "('success','succeeded','failed','unknown_after_crash','payment_required') "
                    "THEN firecrawl_operations.status ELSE 'unknown_after_crash' END, "
                    "execution_id=COALESCE(firecrawl_operations.execution_id, EXCLUDED.execution_id), "
                    "job_id=COALESCE(firecrawl_operations.job_id, EXCLUDED.job_id), "
                    "reservation_id=COALESCE(firecrawl_operations.reservation_id, EXCLUDED.reservation_id), "
                    "metadata_json=CASE WHEN firecrawl_operations.status IN "
                    "('success','succeeded','failed','unknown_after_crash','payment_required') "
                    "THEN firecrawl_operations.metadata_json ELSE EXCLUDED.metadata_json END"
                ),
                {
                    "cost": reservation["cost_units"],
                    "execution_id": execution["id"],
                    "reservation_id": reservation["id"],
                    "operation_key": reservation["operation_key"],
                    "request_fingerprint": reservation.get("request_fingerprint"),
                    "metadata": json.dumps(metadata),
                },
            )
            reconciled += 1
    return reconciled


def reconcile_stale_discovery_reservations(
    db: Session,
    run_id: str,
) -> int:
    """Reconcile a discovery run using its durable run/cycle identity."""
    with Session(independent_bind(db)) as session:
        count = reconcile_discovery_reservations(session, run_id=run_id)
        session.commit()
        return count


def reconcile_stale_direct_firecrawl_reservations(
    db: Session,
    *,
    operation_key: str | None = None,
    stale_after_seconds: int = DIRECT_RESERVATION_STALE_SECONDS,
    reference_time: datetime | None = None,
) -> int:
    """Account for abandoned direct Firecrawl reservations.

    Direct calls have no execution or enrichment lease to act as an
    ownership fence.  Only a committed reservation with both ownership
    locators NULL, an unresolved status, and a stale creation time qualifies.
    The usage increment is intentionally retained and no provider call or
    refund is attempted.  An optional operation key scopes reconciliation to
    one idempotent direct request before a replay is considered.
    """
    if stale_after_seconds < 0:
        raise ValueError("stale_after_seconds must be non-negative")
    cutoff = (reference_time or datetime.now(timezone.utc))
    from datetime import timedelta

    cutoff -= timedelta(seconds=stale_after_seconds)
    key_clause = " AND operation_key=:operation_key" if operation_key is not None else ""
    params: dict[str, Any] = {"cutoff": cutoff}
    if operation_key is not None:
        params["operation_key"] = operation_key

    with Session(independent_bind(db)) as session:
        rows = session.execute(
            text(
                "SELECT * FROM provider_reservations "
                "WHERE provider_name IN ('firecrawl','firecrawl_monthly') "
                "AND execution_id IS NULL AND job_id IS NULL "
                "AND status IN ('reserved','in_flight') "
                "AND NOT EXISTS (SELECT 1 FROM firecrawl_operations o "
                "WHERE o.operation_key=provider_reservations.operation_key "
                "AND o.result_state IN ('staged','consumed')) "
                "AND created_at <= :cutoff" + key_clause + " FOR UPDATE"
            ),
            params,
        ).mappings().all()
        for row in rows:
            metadata = {
                "reason": "stale_direct_reservation_reconciled",
                "operation_key": row["operation_key"],
                "reservation_id": row["id"],
            }
            session.execute(
                text(
                    "UPDATE provider_reservations SET status='unknown_after_crash', "
                    "billing_metadata=COALESCE(billing_metadata, '{}'::jsonb) "
                    "|| CAST(:metadata AS jsonb) "
                    "WHERE id=:reservation_id AND status IN ('reserved','in_flight')"
                ),
                {"reservation_id": row["id"], "metadata": json.dumps(metadata)},
            )
            session.execute(
                text(
                    "INSERT INTO firecrawl_operations "
                    "(operation,cost_units,status,created_at,execution_id,job_id,"
                    "reservation_id,operation_key,metadata_json) "
                    "VALUES (:operation,:cost,'unknown_after_crash',clock_timestamp(),NULL,NULL,"
                    ":reservation_id,:operation_key,CAST(:metadata AS jsonb)) "
                    "ON CONFLICT (operation_key) DO UPDATE SET "
                    "status=CASE WHEN firecrawl_operations.status IN "
                    "('success','succeeded','failed','unknown_after_crash','payment_required') "
                    "THEN firecrawl_operations.status ELSE 'unknown_after_crash' END, "
                    "reservation_id=COALESCE(firecrawl_operations.reservation_id, EXCLUDED.reservation_id), "
                    "metadata_json=CASE WHEN firecrawl_operations.status IN "
                    "('success','succeeded','failed','unknown_after_crash','payment_required') "
                    "THEN firecrawl_operations.metadata_json ELSE EXCLUDED.metadata_json END"
                ),
                {
                    "operation": "discovery" if row["provider_name"] == "firecrawl" else "enrichment",
                    "cost": row["cost_units"],
                    "reservation_id": row["id"],
                    "operation_key": row["operation_key"],
                    "metadata": json.dumps(metadata),
                },
            )
        session.commit()
        return len(rows)


# Short compatibility alias for callers that use the generic reconciler name.
reconcile_stale_direct_reservations = reconcile_stale_direct_firecrawl_reservations


def settle_provider_reservation(
    receipt: ProviderReservationReceipt | str,
    status: str,
    *,
    db: Session | None = None,
    metadata: dict[str, Any] | None = None,
    require_transition: bool = False,
    allow_reserved_terminal: bool = False,
    reserved_only: bool = False,
    recovery_job_id: int | None = None,
    recovery_token: str | None = None,
    discovery_run_id: str | None = None,
    discovery_owner_token: str | None = None,
) -> ProviderReservationReceipt | None:
    """Fence and settle a provider reservation exactly once.

    The helper owns only its independent reservation transaction.  It never
    commits the caller's SQLAlchemy session.  Re-settling a terminal receipt
    is idempotent and returns the already durable terminal state.
    """

    if status not in RESERVATION_STATUSES:
        raise ValueError(f"Unknown provider reservation status: {status}")
    reservation_id = receipt if isinstance(receipt, str) else receipt.reservation_id

    transitioned = False
    result: ProviderReservationReceipt | None = None
    with Session(independent_bind(db)) as session:
        if discovery_run_id is not None:
            parent = session.execute(text("""
                SELECT owner_token, owner_expires_at, status
                FROM firecrawl_discovery_runs
                WHERE run_id=:run_id FOR UPDATE
            """), {"run_id": discovery_run_id}).mappings().first()
            if (
                parent is None
                or parent["status"] != "running"
                or parent["owner_token"] != discovery_owner_token
                or parent["owner_expires_at"] is None
                or parent["owner_expires_at"] <= datetime.now(timezone.utc)
            ):
                session.rollback()
                return None
        # Every enrichment path locks job ownership before its reservation.
        # The initial read is only a locator; re-read the receipt under lock.
        locator = _receipt_row(session, reservation_id)
        if locator is None:
            return None
        if discovery_run_id is not None:
            child = session.execute(text("""
                SELECT cycle_id, status, ownership_token, ownership_expires_at
                FROM search_execution WHERE id=:execution_id FOR UPDATE
            """), {"execution_id": locator["execution_id"]}).mappings().first()
            if (
                child is None
                or child["cycle_id"] != discovery_run_id
                or child["status"] not in {"selected", "started", "succeeded"}
                or (
                    child["status"] in {"selected", "started"}
                    and (
                        child["ownership_token"] != discovery_owner_token
                        or child["ownership_expires_at"] is None
                        or child["ownership_expires_at"] <= datetime.now(timezone.utc)
                    )
                )
            ):
                session.rollback()
                return None
        job = None
        if locator["job_id"] is not None:
            job = session.execute(text(
                "SELECT *, lease_expires_at > clock_timestamp() AS live FROM job_enrichments "
                "WHERE job_id=:jid FOR UPDATE"
            ), {"jid": locator["job_id"]}).mappings().first()
        row = session.execute(
            select(ProviderReservationModel.__table__)
            .where(ProviderReservationModel.id == reservation_id)
            .with_for_update()
        ).mappings().first()
        if not isinstance(row, Mapping):
            return None

        current = str(row["status"])
        if reserved_only and current != "reserved":
            return None
        if recovery_job_id is not None:
            if (not job or job["job_id"] != recovery_job_id
                    or job["status"] != "in_progress" or not job["live"]
                    or job["lease_token"] != recovery_token
                    or job["staged_reservation_id"] != reservation_id
                    or not job["staged_content"]
                    or current not in {"in_flight", "succeeded"}):
                return None
        allowed = (
            current == status
            or (current == "reserved" and status == "in_flight")
            or (current == "in_flight" and status in TERMINAL_RESERVATION_STATUSES)
            or (allow_reserved_terminal and current == "reserved" and status in TERMINAL_RESERVATION_STATUSES)
        )
        if require_transition and row["job_id"] is not None:
            lease_live = session.execute(
                text(
                    "SELECT 1 FROM job_enrichments "
                    "WHERE job_id=:job_id AND status='in_progress' "
                    "AND lease_token=:lease_token "
                    "AND lease_expires_at > clock_timestamp() FOR UPDATE"
                ),
                {"job_id": row["job_id"], "lease_token": row["lease_token"]},
            ).scalar()
            if not lease_live:
                session.commit()
                return None
        if allowed and current != status:
            session.execute(
                update(ProviderReservationModel)
                .where(
                    ProviderReservationModel.id == reservation_id,
                    ProviderReservationModel.status == current,
                )
                .values(
                    status=status,
                    billing_metadata=(
                        metadata if current not in TERMINAL_RESERVATION_STATUSES
                        else ProviderReservationModel.billing_metadata
                    ),
                )
            )
            transitioned = True
            row = dict(row)
            row["status"] = status
        else:
            # A terminal receipt is fenced.  Never permit a late worker to
            # overwrite succeeded/unknown/payment_required with another state.
            row = dict(row)

        result = ProviderReservationReceipt.from_mapping(row)
        if require_transition and not transitioned:
            return None
        if transitioned and result.provider_name in {"firecrawl", "firecrawl_monthly"}:
            session.execute(
                text(
                    "INSERT INTO firecrawl_operations "
                    "(operation,cost_units,status,created_at,execution_id,job_id,"
                     "reservation_id,operation_key,request_fingerprint,metadata_json) "
                     "VALUES (:o,:c,:st,clock_timestamp(),:e,:j,:r,:k,:f,CAST(:m AS jsonb)) "
                    "ON CONFLICT (operation_key) DO UPDATE SET "
                    "status=CASE WHEN firecrawl_operations.status IN ('success','succeeded','failed','unknown_after_crash','payment_required') "
                    "THEN firecrawl_operations.status ELSE EXCLUDED.status END, "
                    "execution_id=COALESCE(firecrawl_operations.execution_id, EXCLUDED.execution_id), "
                    "job_id=COALESCE(firecrawl_operations.job_id, EXCLUDED.job_id), "
                    "reservation_id=COALESCE(firecrawl_operations.reservation_id, EXCLUDED.reservation_id), "
                    "metadata_json=CASE WHEN firecrawl_operations.status IN ('success','succeeded','failed','unknown_after_crash','payment_required') "
                    "THEN firecrawl_operations.metadata_json ELSE COALESCE(EXCLUDED.metadata_json, firecrawl_operations.metadata_json) END"
                ),
                {
                    "o": "discovery" if result.provider_name == "firecrawl" else "enrichment",
                    "c": result.cost_units,
                    "st": _operation_status(status),
                    "e": result.execution_id,
                    "j": result.job_id,
                    "r": result.reservation_id,
                    "k": result.operation_key,
                    "f": row.get("request_fingerprint"),
                    "m": json.dumps(metadata) if metadata is not None else None,
                },
            )
        session.commit()

    if transitioned and status == "payment_required":
        latch_firecrawl_payment_required(db)
    return result


def settle_provider_request(
    db: Session,
    receipt: ProviderReservationReceipt | str,
    status: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> ProviderReservationReceipt | None:
    """Argument-order compatibility facade for service-layer callers."""
    return settle_provider_reservation(receipt, status, db=db, metadata=metadata)


def get_firecrawl_budget_state(
    db: Session,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    """Return current-month usage and durable Firecrawl exhaustion state."""

    today = (reference_time or datetime.now(timezone.utc)).date()
    month = today.replace(day=1)
    row = db.execute(
        select(FirecrawlBudgetModel.__table__).where(FirecrawlBudgetModel.month == month)
    ).mappings().first()
    # Dependency-overridden API tests use a MagicMock session; treat its
    # fabricated result as no persisted budget row rather than arithmetic data.
    persisted_row = row if isinstance(row, Mapping) else None
    budget, cap, reserve = (
        policy_values()
        if persisted_row is None
        else (row["account_budget"], row["automation_cap"], row["protected_reserve"])
    )
    used = (
        db.execute(
            text(
                "SELECT COALESCE(SUM(request_count),0) FROM provider_usage "
                "WHERE provider_name IN ('firecrawl','firecrawl_monthly') "
                "AND usage_date>=:m AND usage_date<:m+INTERVAL '1 month'"
            ),
            {"m": month},
        ).scalar()
        or 0
    )
    discovery_used = (
        db.execute(
            text(
                "SELECT COALESCE(SUM(request_count),0) FROM provider_usage "
                "WHERE provider_name='firecrawl' AND usage_date>=:m "
                "AND usage_date<:m+INTERVAL '1 month'"
            ),
            {"m": month},
        ).scalar()
        or 0
    )
    enrichment_used = (
        db.execute(
            text(
                "SELECT COALESCE(SUM(request_count),0) FROM provider_usage "
                "WHERE provider_name='firecrawl_monthly' AND usage_date>=:m "
                "AND usage_date<:m+INTERVAL '1 month'"
            ),
            {"m": month},
        ).scalar()
        or 0
    )
    payment = bool(persisted_row and row["payment_required"]) or bool(
        db.execute(
            text(
                "SELECT EXISTS(SELECT 1 FROM firecrawl_operations "
                "WHERE status='payment_required' AND created_at>=:m "
                "AND created_at<:m+INTERVAL '1 month')"
            ),
            {"m": month},
        ).scalar()
    )
    denied = (
        db.execute(
            text(
                "SELECT COUNT(*) FROM firecrawl_operations WHERE status='denied' "
                "AND created_at>=:m AND created_at<:m+INTERVAL '1 month'"
            ),
            {"m": month},
        ).scalar()
        or 0
    )
    op = db.execute(
        text(
            "SELECT operation, cost_units, status FROM firecrawl_operations "
            "ORDER BY created_at DESC, id DESC LIMIT 1"
        ),
    ).mappings().first()
    last_operation = (
        {"operation": op["operation"], "credits": int(op["cost_units"]), "status": op["status"]}
        if op
        else None
    )
    return {
        "monthly_cap": cap,
        "protected_reserve": reserve,
        "account_budget": budget,
        "monthly_used": int(used),
        "monthly_remaining": max(0, int(cap) - int(used)),
        "firecrawl_monthly_used": int(used),
        "firecrawl_monthly_remaining": max(0, int(cap) - int(used)),
        "discovery_used": int(discovery_used),
        "enrichment_used": int(enrichment_used),
        "firecrawl_discovery_used": int(discovery_used),
        "firecrawl_enrichment_used": int(enrichment_used),
        "quota_denied": int(denied),
        "firecrawl_requests_denied_quota": int(denied),
        "last_operation": last_operation,
        "firecrawl_last_operation": last_operation,
        "payment_required": payment,
        "is_exhausted": payment or int(used) >= int(cap),
        "policy_compatible": persisted_row is None or policy_matches(row),
        "reserved": reserve,
    }
