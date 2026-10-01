"""Firecrawl quota observability and budget state helper."""
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.config import settings

def get_firecrawl_budget_state(db: Session, reference_time: datetime | None = None) -> dict[str, Any]:
    """Returns current month's Firecrawl credit consumption, remaining automation credits, and reserve metrics."""
    now_utc = reference_time or datetime.now(timezone.utc)
    today = now_utc.date()

    monthly_cap = min(settings.firecrawl_monthly_automation_cap, settings.firecrawl_monthly_budget)

    stmt = text("""
        SELECT
            COALESCE(SUM(CASE WHEN provider_name = 'firecrawl' THEN request_count ELSE 0 END), 0) AS discovery_used,
            COALESCE(SUM(CASE WHEN provider_name = 'firecrawl_monthly' THEN request_count ELSE 0 END), 0) AS enrichment_used,
            COALESCE(SUM(request_count), 0) AS total_used
        FROM provider_usage
        WHERE provider_name IN ('firecrawl', 'firecrawl_monthly')
          AND usage_date >= CAST(DATE_TRUNC('month', CAST(:d AS date)) AS date)
          AND usage_date < CAST(DATE_TRUNC('month', CAST(:d AS date)) + INTERVAL '1 month' AS date)
    """)
    row = db.execute(stmt, {'d': today}).mappings().first()

    discovery_used = int(row['discovery_used']) if row else 0
    enrichment_used = int(row['enrichment_used']) if row else 0
    total_used = int(row['total_used']) if row else 0
    remaining = max(0, monthly_cap - total_used)

    return {
        'monthly_cap': monthly_cap,
        'protected_reserve': settings.firecrawl_reserved_credits,
        'account_budget': settings.firecrawl_monthly_budget,
        'firecrawl_monthly_used': total_used,
        'firecrawl_monthly_remaining': remaining,
        'firecrawl_discovery_used': discovery_used,
        'firecrawl_enrichment_used': enrichment_used,
        'is_exhausted': total_used >= monthly_cap,
    }
