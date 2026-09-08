import pytest
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.services.ingestion import run_ingestion, RateLimitExceeded
from app.schemas.job_search import JobSearchQuery
from app.db.models.provider_usage import ProviderUsageModel

class DummyProvider:
    def search_jobs(self, query):
        return []

def test_usage_limits(db_session: Session, monkeypatch):
    from app.core.config import settings
    # We use jooble to bypass Pydantic extra field validation
    monkeypatch.setattr(settings, "jooble_daily_limit", 2)

    query = JobSearchQuery(keywords="k", location="l")
    provider = DummyProvider()

    # Request 1 (should succeed)
    res1 = run_ingestion(db_session, "jooble", provider, query)
    assert res1.failed == 0

    # Request 2 (should succeed)
    res2 = run_ingestion(db_session, "jooble", provider, query)
    assert res2.failed == 0

    # Request 3 (should fail due to limit)
    with pytest.raises(RateLimitExceeded):
        run_ingestion(db_session, "jooble", provider, query)

    # Verify DB state
    usage = db_session.query(ProviderUsageModel).filter_by(provider_name="jooble").first()
    assert usage.request_count == 2

from app.providers.exceptions import ProviderConfigurationError
class FailingConfigProvider:
    def validate_config(self):
        raise ProviderConfigurationError("Configuration missing")
    def search_jobs(self, query):
        return []

def test_config_failure_does_not_consume_quota(db_session: Session, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "jooble_daily_limit", 10)
    query = JobSearchQuery(keywords="k", location="l")
    provider = FailingConfigProvider()

    with pytest.raises(ProviderConfigurationError, match="Configuration missing"):
        run_ingestion(db_session, "jooble", provider, query)

    usage = db_session.query(ProviderUsageModel).filter_by(provider_name="jooble").first()
    assert usage is None or usage.request_count == 0
