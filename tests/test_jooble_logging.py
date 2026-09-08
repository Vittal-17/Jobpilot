import pytest
import logging
import respx
import httpx
from app.providers.jooble import JoobleProvider
from app.schemas.job_search import JobSearchQuery
from app.providers.exceptions import ProviderNetworkError
from app.core.config import settings

def test_jooble_secret_logging_leak(monkeypatch, caplog):
    monkeypatch.setattr(settings, "jooble_api_key", "TEST_JOOBLE_SECRET_123456")
    caplog.set_level(logging.ERROR)

    provider = JoobleProvider()
    query = JobSearchQuery(keywords="test", location="test")

    with respx.mock:
        # Cause a realistic RequestError
        respx.post("https://jooble.org/api/TEST_JOOBLE_SECRET_123456").mock(
            side_effect=httpx.RequestError("Mocked connection failure")
        )

        with pytest.raises(ProviderNetworkError):
            provider.search_jobs(query)

    # Check all captured log records
    for record in caplog.records:
        assert "TEST_JOOBLE_SECRET_123456" not in record.message
        assert "TEST_JOOBLE_SECRET_123456" not in str(record.exc_info)
