from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
from datetime import datetime, timezone
import logging
from app.providers.base import JobProvider
from app.schemas.job_search import JobSearchQuery, IngestionResult
from app.db.repository.job_repository import save_job
from app.core.config import settings

logger = logging.getLogger(__name__)

class RateLimitExceeded(Exception):
    pass

class DatabaseUnavailable(Exception):
    pass

def acquire_provider_request_slot(db: Session, provider_name: str) -> bool:
    """Acquires a quota slot atomically. Returns True if request is allowed, False if over limit."""
    from app.services.quota_policy import get_provider_policy

    try:
        policy = get_provider_policy(provider_name)
    except ValueError as e:
        logger.error(str(e))
        raise ValueError(f"ConfigurationError: {str(e)}")

    minute_limit = policy.get_effective_limit('minute')
    daily_limit = policy.get_effective_limit('daily')
    lifetime_limit = policy.get_effective_limit('lifetime')

    if daily_limit is not None and daily_limit <= 0:
        logger.warning(f"Provider {provider_name} has non-positive daily limit, blocking request.")
        return False

    if minute_limit is not None and minute_limit <= 0:
        logger.warning(f"Provider {provider_name} has non-positive minute limit, blocking request.")
        return False

    now_utc = datetime.now(timezone.utc)
    today = now_utc.date()
    current_minute = now_utc.replace(second=0, microsecond=0)

    try:
        if minute_limit is not None:
            stmt_minute = text("""
                INSERT INTO provider_minute_usage (provider_name, usage_minute, request_count)
                VALUES (:p, :m, 1)
                ON CONFLICT (provider_name, usage_minute)
                DO UPDATE SET request_count = provider_minute_usage.request_count + 1
                RETURNING request_count
            """)
            res_minute = db.execute(stmt_minute, {"p": provider_name, "m": current_minute}).scalar()
            if res_minute > minute_limit:
                db.rollback()
                return False

        if daily_limit is not None:
            stmt_daily = text("""
                INSERT INTO provider_usage (provider_name, usage_date, request_count)
                VALUES (:p, :d, 1)
                ON CONFLICT (provider_name, usage_date)
                DO UPDATE SET request_count = provider_usage.request_count + 1
                RETURNING request_count
            """)
            res_daily = db.execute(stmt_daily, {"p": provider_name, "d": today}).scalar()
            if res_daily > daily_limit:
                db.rollback()
                return False

        if lifetime_limit is not None:
            stmt_lifetime = text("""
                INSERT INTO provider_state (provider_name, lifetime_count)
                VALUES (:p, 1)
                ON CONFLICT (provider_name)
                DO UPDATE SET lifetime_count = provider_state.lifetime_count + 1
                RETURNING lifetime_count
            """)
            res_lifetime = db.execute(stmt_lifetime, {"p": provider_name}).scalar()
            if res_lifetime > lifetime_limit:
                db.rollback()
                return False

        db.commit()
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to acquire quota due to database failure: {e}")
        raise DatabaseUnavailable("Quota database unavailable")

def run_ingestion(db: Session, provider_name: str, provider: JobProvider, query: JobSearchQuery) -> IngestionResult:
    result = IngestionResult(provider=provider_name)

    # Provider configuration failure (e.g. missing credentials) should happen
    # before quota acquisition. Wait, the provider is passed in.
    # We will assume provider instantiation validates it, or we call it explicitly.
    if hasattr(provider, "validate_config"):
        provider.validate_config()

    if not acquire_provider_request_slot(db, provider_name):
        logger.warning(f"Provider {provider_name} exceeded daily or lifetime limit")
        raise RateLimitExceeded(f"Limit exceeded for {provider_name}")

    from app.providers.exceptions import ProviderError
    try:
        jobs = provider.search_jobs(query)
    except ProviderError as e:
        logger.error(f"Provider {provider_name} failed: failure_type={e.__class__.__name__}")
        result.failed = 1
        return result
    except Exception as e:
        logger.error(f"Provider {provider_name} failed due to unexpected error: failure_type={e.__class__.__name__}")
        result.failed = 1
        return result

    result.fetched = len(jobs)

    for job in jobs:
        try:
            with db.begin_nested():
                save_job(db, job)
            result.created += 1
        except IntegrityError as e:
            if hasattr(e.orig, "sqlstate") and e.orig.sqlstate == "23505":
                result.duplicates += 1
            else:
                logger.warning(f"Failed to persist job {job.source_job_id} due to non-duplicate integrity error")
                result.invalid += 1
        except Exception:
            logger.warning(f"Failed to persist job {job.source_job_id} due to unexpected error")
            result.invalid += 1

    db.commit()
    return result
