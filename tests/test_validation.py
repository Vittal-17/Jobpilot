import pytest
from app.providers.jooble import JoobleProvider
from app.schemas.job_search import JobSearchQuery
import httpx
import respx

def test_missing_provider_id(monkeypatch, caplog):
    import logging
    caplog.set_level(logging.WARNING)
    monkeypatch.setattr("app.providers.jooble.settings.jooble_api_key", "dummy")
    query = JobSearchQuery(keywords="test", location="test")
    provider = JoobleProvider()

    mock_resp = {
        "jobs": [
            {
                "title": "Missing ID Job",
                "company": "Test Co",
                "location": "Bangalore"
            }
        ]
    }

    with respx.mock:
        respx.post("https://jooble.org/api/dummy").mock(return_value=httpx.Response(200, json=mock_resp))
        jobs = provider.search_jobs(query)

        assert len(jobs) == 0, "Should skip job with missing ID"
