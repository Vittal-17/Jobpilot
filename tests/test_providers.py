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
    monkeypatch.setattr(settings, "jooble_in_api_key", "test_jooble")

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
    assert jobs[0].description_is_snippet is True

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

    respx.post("https://in.jooble.org/api/test_jooble").mock(return_value=httpx.Response(200, json=mock_resp))

    jobs = provider.search_jobs(query)
    assert len(jobs) == 1
    assert jobs[0].title == "Python Dev"
    assert jobs[0].source == "jooble"
    assert jobs[0].source_job_id == "abc"
    assert jobs[0].description_is_snippet is True


@respx.mock
def test_adzuna_employment_type_mapping():
    provider = AdzunaProvider()
    query = JobSearchQuery(keywords="Python", location="Bangalore", radius_km=40, page=1, page_size=20)

    # 1. contract_time = full_time -> Full-time
    mock_resp_1 = {
        "results": [
            {
                "id": 1,
                "title": "Python Dev",
                "company": {"display_name": "Test Co"},
                "contract_time": "full_time",
                "contract_type": "permanent",
                "redirect_url": "http://test/1"
            }
        ]
    }
    respx.get("https://api.adzuna.com/v1/api/jobs/in/search/1").mock(return_value=httpx.Response(200, json=mock_resp_1))
    jobs = provider.search_jobs(query)
    assert len(jobs) == 1
    assert jobs[0].employment_type == "Full-time"

    # 2. contract_time = part_time -> Part-time
    mock_resp_2 = {
        "results": [
            {
                "id": 2,
                "title": "Python Dev",
                "company": {"display_name": "Test Co"},
                "contract_time": "part_time",
                "redirect_url": "http://test/2"
            }
        ]
    }
    respx.get("https://api.adzuna.com/v1/api/jobs/in/search/1").mock(return_value=httpx.Response(200, json=mock_resp_2))
    jobs = provider.search_jobs(query)
    assert len(jobs) == 1
    assert jobs[0].employment_type == "Part-time"

    # 3. Fallback to contract_type = contract -> Contract
    mock_resp_3 = {
        "results": [
            {
                "id": 3,
                "title": "Python Dev",
                "company": {"display_name": "Test Co"},
                "contract_type": "contract",
                "redirect_url": "http://test/3"
            }
        ]
    }
    respx.get("https://api.adzuna.com/v1/api/jobs/in/search/1").mock(return_value=httpx.Response(200, json=mock_resp_3))
    jobs = provider.search_jobs(query)
    assert len(jobs) == 1
    assert jobs[0].employment_type == "Contract"

    # 4. Fallback to contract_type = permanent -> Permanent
    mock_resp_4 = {
        "results": [
            {
                "id": 4,
                "title": "Python Dev",
                "company": {"display_name": "Test Co"},
                "contract_type": "permanent",
                "redirect_url": "http://test/4"
            }
        ]
    }
    respx.get("https://api.adzuna.com/v1/api/jobs/in/search/1").mock(return_value=httpx.Response(200, json=mock_resp_4))
    jobs = provider.search_jobs(query)
    assert len(jobs) == 1
    assert jobs[0].employment_type == "Permanent"

    # 5. Neither provided -> None (not fabricated)
    mock_resp_5 = {
        "results": [
            {
                "id": 5,
                "title": "Python Dev",
                "company": {"display_name": "Test Co"},
                "redirect_url": "http://test/5"
            }
        ]
    }
    respx.get("https://api.adzuna.com/v1/api/jobs/in/search/1").mock(return_value=httpx.Response(200, json=mock_resp_5))
    jobs = provider.search_jobs(query)
    assert len(jobs) == 1
    assert jobs[0].employment_type is None

    # 6. Unknown field behavior (e.g. seasonal) -> formatted cleanly
    mock_resp_6 = {
        "results": [
            {
                "id": 6,
                "title": "Python Dev",
                "company": {"display_name": "Test Co"},
                "contract_time": "seasonal",
                "redirect_url": "http://test/6"
            }
        ]
    }
    respx.get("https://api.adzuna.com/v1/api/jobs/in/search/1").mock(return_value=httpx.Response(200, json=mock_resp_6))
    jobs = provider.search_jobs(query)
    assert len(jobs) == 1
    assert jobs[0].employment_type == "Seasonal"

    # 7. Mixed: unrecognized contract_time ("seasonal") with recognized contract_type ("contract") -> Contract
    mock_resp_7 = {
        "results": [
            {
                "id": 7,
                "title": "Python Dev",
                "company": {"display_name": "Test Co"},
                "contract_time": "seasonal",
                "contract_type": "contract",
                "redirect_url": "http://test/7"
            }
        ]
    }
    respx.get("https://api.adzuna.com/v1/api/jobs/in/search/1").mock(return_value=httpx.Response(200, json=mock_resp_7))
    jobs = provider.search_jobs(query)
    assert len(jobs) == 1
    assert jobs[0].employment_type == "Contract"
