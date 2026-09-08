import pytest
import logging
from app.providers.adzuna import AdzunaProvider
from app.schemas.job_search import JobSearchQuery
import httpx
import respx
from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings

def test_secret_leakage_in_exceptions(monkeypatch):
    monkeypatch.setattr(settings, "adzuna_app_id", "TEST_ID_123")
    monkeypatch.setattr(settings, "adzuna_app_key", "TEST_SECRET_ADZUNA")

    query = JobSearchQuery(keywords="test", location="test")
    provider = AdzunaProvider()

    # Mock network error
    with respx.mock:
        respx.get("https://api.adzuna.com/v1/api/jobs/in/search/1").mock(return_value=httpx.Response(500))

        with pytest.raises(Exception) as exc:
            provider.search_jobs(query)

        err_msg = str(exc.value)
        assert "TEST_SECRET_ADZUNA" not in err_msg, "Secret leaked in exception string!"

def test_malformed_response_handled(monkeypatch):
    query = JobSearchQuery(keywords="test", location="test")
    provider = AdzunaProvider()

    with respx.mock:
        # Mock malformed response
        respx.get("https://api.adzuna.com/v1/api/jobs/in/search/1").mock(return_value=httpx.Response(200, json={"wrong": "schema"}))

        with pytest.raises(Exception) as exc:
            provider.search_jobs(query)

        assert "AdzunaProviderSchemaError" in str(exc.value)

def test_database_isolation():
    from tests.conftest import TEST_DATABASE_URL
    # P0.1 safety test
    assert "_test" in TEST_DATABASE_URL, "Test database is not isolated from production!"

def test_malformed_item_skipped(monkeypatch):
    monkeypatch.setattr(settings, "adzuna_app_id", "TEST_ID_123")
    monkeypatch.setattr(settings, "adzuna_app_key", "TEST_SECRET_ADZUNA")
    query = JobSearchQuery(keywords="test", location="test")
    provider = AdzunaProvider()

    with respx.mock:
        # One valid item, one integer, one string
        payload = {
            "results": [
                {"id": "1", "title": "Dev", "company": {"display_name": "A"}, "created": "2024-01-01T00:00:00Z"},
                123,
                "not a dict"
            ]
        }
        respx.get("https://api.adzuna.com/v1/api/jobs/in/search/1").mock(return_value=httpx.Response(200, json=payload))

        jobs = provider.search_jobs(query)
        # Should parse exactly the 1 valid item and skip the rest
        assert len(jobs) == 1
        assert jobs[0].title == "Dev"
