import pytest
import respx
import httpx
from datetime import datetime, timezone
from app.providers.adzuna import AdzunaProvider
from app.providers.jooble import JoobleProvider
from app.schemas.job_search import JobSearchQuery
from app.models.job import Job
from app.core.config import settings

@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    monkeypatch.setattr(settings, "adzuna_app_id", "test_id")
    monkeypatch.setattr(settings, "adzuna_app_key", "test_key")
    monkeypatch.setattr(settings, "jooble_api_key", "test_jooble")

@respx.mock
def test_adzuna_success():
    query = JobSearchQuery(keywords="Python", location="Bangalore", radius_km=40, page=1, page_size=20)
    provider = AdzunaProvider()

    mock_resp = {
        "results": [
            {
                "id": 123,
                "title": "Python Dev",
                "company": {"display_name": "Test Co"},
                "location": {"display_name": "Bangalore"},
                "description": "Snippet",
                "salary_min": 10000,
                "redirect_url": "http://test",
                "created": "2023-01-01T10:00:00Z"
            }
        ]
    }

    respx.get("https://api.adzuna.com/v1/api/jobs/in/search/1").mock(return_value=httpx.Response(200, json=mock_resp))

    jobs = provider.search_jobs(query)
    assert len(jobs) == 1
    assert jobs[0].title == "Python Dev"
    assert jobs[0].source == "adzuna"
    assert jobs[0].source_job_id == "123"
    assert jobs[0].salary_min == 10000

@respx.mock
def test_jooble_success():
    query = JobSearchQuery(keywords="Python", location="Bangalore", radius_km=40, page=1, page_size=20)
    provider = JoobleProvider()

    mock_resp = {
        "jobs": [
            {
                "id": "abc",
                "title": "Python Dev",
                "company": "Test Co",
                "location": "Bangalore",
                "snippet": "Snippet",
                "link": "http://test",
                "updated": "2023-01-01T10:00:00"
            }
        ]
    }

    respx.post("https://jooble.org/api/test_jooble").mock(return_value=httpx.Response(200, json=mock_resp))

    jobs = provider.search_jobs(query)
    assert len(jobs) == 1
    assert jobs[0].title == "Python Dev"
    assert jobs[0].source == "jooble"
    assert jobs[0].source_job_id == "abc"
