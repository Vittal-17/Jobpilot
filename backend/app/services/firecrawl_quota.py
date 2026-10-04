import logging
import math
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.config import settings

logger = logging.getLogger(__name__)

def calculate_search_credits(limit: int = 10) -> int:
    """Calculate Firecrawl Search credit cost: 2 credits per 10 results, rounded up."""
    if limit <= 0:
        return 2
    return max(2, math.ceil(limit / 10) * 2)

def record_firecrawl_operation(
    operation: str,
    cost_units: int,
    status: str,
) -> None:
    """
    Record an operational event in firecrawl_operations.
    Uses an independent transaction so that events (such as quota denials after transaction rollback)
    are committed reliably without polluting or depending on caller transaction state.
    When status == 'payment_required', acquires the Firecrawl advisory lock so that concurrent
    reservations serialize behind this exhaustion transition and immediately observe it.
    Catches all exceptions internally to guarantee telemetry recording failures cannot corrupt
    or crash caller transaction flows.
    """
    from app.db.database import SessionLocal
    try:
        with SessionLocal() as session:
            try:
                if status == "payment_required":
                    session.execute(
                        text("SELECT pg_advisory_xact_lock(hashtext('provider_quota'), hashtext('firecrawl'))")
                    )
                session.execute(
                    text("""
                        INSERT INTO firecrawl_operations (operation, cost_units, status, created_at)
                        VALUES (:op, :cost, :status, clock_timestamp())
                    """),
                    {"op": operation, "cost": cost_units, "status": status}
                )
                session.commit()
            except Exception as e:
                session.rollback()
                logger.error(f"Failed to record Firecrawl operation event: {e}")
    except Exception as e:
        logger.error(f"Database session error recording Firecrawl operation event: {e}")

def get_firecrawl_budget_state(db: Session, reference_time: datetime | None = None) -> dict[str, Any]:
    """Returns current month's Firecrawl credit consumption, remaining automation credits, reserve metrics, denials, and last operation."""
    now_utc = reference_time or datetime.now(timezone.utc)
    today = now_utc.date()

    monthly_cap = min(settings.firecrawl_monthly_automation_cap, settings.firecrawl_monthly_budget)

    # 1. Authoritative credit consumption from provider_usage
    stmt_usage = text("""
        SELECT
            COALESCE(SUM(CASE WHEN provider_name = 'firecrawl' THEN request_count ELSE 0 END), 0) AS discovery_used,
            COALESCE(SUM(CASE WHEN provider_name = 'firecrawl_monthly' THEN request_count ELSE 0 END), 0) AS enrichment_used,
            COALESCE(SUM(request_count), 0) AS total_used
        FROM provider_usage
        WHERE provider_name IN ('firecrawl', 'firecrawl_monthly')
          AND usage_date >= CAST(DATE_TRUNC('month', CAST(:d AS date)) AS date)
          AND usage_date < CAST(DATE_TRUNC('month', CAST(:d AS date)) + INTERVAL '1 month' AS date)
    """)
    usage_row = db.execute(stmt_usage, {'d': today}).mappings().first()

    discovery_used = int(usage_row['discovery_used']) if usage_row else 0
    enrichment_used = int(usage_row['enrichment_used']) if usage_row else 0
    total_used = int(usage_row['total_used']) if usage_row else 0
    remaining = max(0, monthly_cap - total_used)

    # 2. Monthly quota denials from firecrawl_operations
    stmt_denials = text("""
        SELECT COUNT(*) AS quota_denied
        FROM firecrawl_operations
        WHERE status = 'denied'
          AND created_at >= CAST(DATE_TRUNC('month', CAST(:d AS date)) AS timestamp with time zone)
          AND created_at < CAST(DATE_TRUNC('month', CAST(:d AS date)) + INTERVAL '1 month' AS timestamp with time zone)
    """)
    denials_row = db.execute(stmt_denials, {'d': today}).mappings().first()
    quota_denied = int(denials_row['quota_denied']) if denials_row else 0

    # 3. Deterministic last operation overall (ORDER BY created_at DESC, id DESC)
    stmt_last_op = text("""
        SELECT operation, cost_units, status, created_at
        FROM firecrawl_operations
        ORDER BY created_at DESC, id DESC
        LIMIT 1
    """)
    op_row = db.execute(stmt_last_op).mappings().first()
    last_op = None
    if op_row:
        last_op = {
            'operation': str(op_row['operation']),
            'credits': int(op_row['cost_units']),
            'status': str(op_row['status']),
            'timestamp': op_row['created_at'].isoformat() if op_row['created_at'] else None,
        }

    # 4. Check for active payment_required operational exhaustion state
    stmt_exhaustion_check = text("""
        SELECT 1 FROM firecrawl_operations
        WHERE status = 'payment_required'
          AND created_at >= CAST(DATE_TRUNC('month', CAST(:d AS date)) AS timestamp with time zone)
          AND created_at < CAST(DATE_TRUNC('month', CAST(:d AS date)) + INTERVAL '1 month' AS timestamp with time zone)
        LIMIT 1
    """)
    has_payment_required = db.execute(stmt_exhaustion_check, {'d': today}).scalar() is not None
    is_exhausted = (total_used >= monthly_cap) or has_payment_required

    return {
        'monthly_cap': monthly_cap,
        'protected_reserve': settings.firecrawl_reserved_credits,
        'account_budget': settings.firecrawl_monthly_budget,
        'firecrawl_monthly_used': total_used,
        'firecrawl_monthly_remaining': remaining,
        'firecrawl_discovery_used': discovery_used,
        'firecrawl_enrichment_used': enrichment_used,
        'firecrawl_requests_denied_quota': quota_denied,
        'firecrawl_last_operation': last_op,
        'is_exhausted': is_exhausted,
        # Canonical aliases
        'monthly_used': total_used,
        'monthly_remaining': remaining,
        'reserved': settings.firecrawl_reserved_credits,
        'discovery_used': discovery_used,
        'enrichment_used': enrichment_used,
        'quota_denied': quota_denied,
        'last_operation': last_op,
    }
