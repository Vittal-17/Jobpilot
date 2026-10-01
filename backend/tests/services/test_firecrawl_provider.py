import pytest
import respx
import httpx
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.core.config import settings
from app.models.job import Job
from app.providers.types import ProviderName
from app.providers.registry import PROVIDER_PRIORITY, create_provider, is_provider_enabled
from app.providers.base import JobProvider
from app.providers.firecrawl import FirecrawlProvider, calculate_search_credits
from app.providers.exceptions import (
    ProviderConfigurationError,
    ProviderHTTPError,
    ProviderNetworkError,
    ProviderPayloadError,
    ProviderTimeout,
)
from app.schemas.job_search import JobSearchQuery
from app.services.scraper.firecrawl import FirecrawlClient, FirecrawlError
from app.services.ingestion import run_ingestion, RateLimitExceeded
from app.db.database import SessionLocal
from app.db.models.job import JobModel
from app.db.models.job_source import JobSourceModel
from app.db.models.job_enrichment import JobEnrichmentModel
from app.db.repository.job_repository import save_job
from app.services.firecrawl_quota import get_firecrawl_budget_state
from app.services.eligibility import is_fresher_eligible
from app.schemas.job import JobResponse
from app.schemas.match import RecommendationPreferences
from app.services.matching_service import calculate_match
from sqlalchemy import text


@pytest.fixture(autouse=True)
def restore_firecrawl_env():
    orig_key = settings.firecrawl_api_key
    orig_enabled = settings.firecrawl_enabled
    orig_disc_enabled = getattr(settings, "firecrawl_discovery_enabled", False)
    orig_cap = settings.firecrawl_monthly_automation_cap
    orig_budget = settings.firecrawl_monthly_budget

    settings.firecrawl_api_key = "test_fc_key"
    settings.firecrawl_enabled = True
    settings.firecrawl_discovery_enabled = True
    settings.firecrawl_monthly_automation_cap = 900
    settings.firecrawl_monthly_budget = 1000

    db = SessionLocal()
    db.execute(text("DELETE FROM provider_usage WHERE provider_name IN ('firecrawl', 'firecrawl_monthly')"))
    db.execute(text("DELETE FROM job_enrichments WHERE url LIKE '%firecrawl_test%'"))
    db.execute(text("DELETE FROM job_sources WHERE source = 'firecrawl' OR url LIKE '%firecrawl_test%'"))
    db.execute(text("DELETE FROM jobs WHERE source = 'firecrawl' OR url LIKE '%firecrawl_test%'"))
    db.commit()
    db.close()

    try:
        yield
    finally:
        db = SessionLocal()
        db.execute(text("DELETE FROM provider_usage WHERE provider_name IN ('firecrawl', 'firecrawl_monthly')"))
        db.execute(text("DELETE FROM job_enrichments WHERE url LIKE '%firecrawl_test%'"))
        db.execute(text("DELETE FROM job_sources WHERE source = 'firecrawl' OR url LIKE '%firecrawl_test%'"))
        db.execute(text("DELETE FROM jobs WHERE source = 'firecrawl' OR url LIKE '%firecrawl_test%'"))
        db.commit()
        db.close()

        settings.firecrawl_api_key = orig_key
        settings.firecrawl_enabled = orig_enabled
        settings.firecrawl_discovery_enabled = orig_disc_enabled
        settings.firecrawl_monthly_automation_cap = orig_cap
        settings.firecrawl_monthly_budget = orig_budget


def test_provider_name_enum_contains_firecrawl():
    assert ProviderName.FIRECRAWL == "firecrawl"
    assert ProviderName.FIRECRAWL.value == "firecrawl"
    assert [p.value for p in ProviderName] == ["adzuna", "jooble", "firecrawl"]


def test_provider_registry_and_factory():
    # Factory instantiation
    provider = create_provider(ProviderName.FIRECRAWL)
    assert isinstance(provider, FirecrawlProvider)
    assert isinstance(provider, JobProvider)

    # Provider priority for generic search cycle is Adzuna then Jooble.
    # Firecrawl is isolated from continuous search cycle to protect its 900-credit pool.
    assert PROVIDER_PRIORITY == (
        ProviderName.ADZUNA,
        ProviderName.JOOBLE,
    )

    # is_provider_enabled follows firecrawl_discovery_enabled
    settings.firecrawl_discovery_enabled = True
    assert is_provider_enabled(ProviderName.FIRECRAWL) is True

    settings.firecrawl_discovery_enabled = False
    assert is_provider_enabled(ProviderName.FIRECRAWL) is False


def test_calculate_search_credits():
    assert calculate_search_credits(10) == 2
    assert calculate_search_credits(5) == 2
    assert calculate_search_credits(1) == 2
    assert calculate_search_credits(0) == 2
    assert calculate_search_credits(-5) == 2
    assert calculate_search_credits(11) == 4
    assert calculate_search_credits(20) == 4
    assert calculate_search_credits(21) == 6


@respx.mock
def test_firecrawl_client_search_success():
    client = FirecrawlClient(api_key="test-key")

    mock_resp = {
        "success": True,
        "data": [
            {
                "url": "https://example.com/job/1",
                "title": "Python Developer at Acme",
                "description": "Looking for Python fresher in Bengaluru."
            },
            {
                "url": "https://example.com/job/2",
                "title": "Backend Engineer - Globex",
                "description": "FastAPI and Postgres experience needed."
            }
        ]
    }
    route = respx.post("https://api.firecrawl.dev/v2/search").mock(
        return_value=httpx.Response(200, json=mock_resp)
    )

    results = client.search(query="Python Developer Bengaluru", limit=10)
    assert len(results) == 2
    assert results[0]["title"] == "Python Developer at Acme"
    assert results[1]["url"] == "https://example.com/job/2"
    assert route.called
    assert route.calls.last.request.headers["authorization"] == "Bearer test-key"


@respx.mock
def test_firecrawl_client_search_empty_and_whitespace_query():
    client = FirecrawlClient(api_key="test-key")
    route = respx.post("https://api.firecrawl.dev/v2/search").mock(return_value=httpx.Response(200, json={"success": True, "data": []}))

    assert client.search("") == []
    assert client.search("   ") == []
    assert not route.called


@respx.mock
def test_firecrawl_client_search_error_cases():
    client = FirecrawlClient(api_key="test-key")

    # 402 Payment Required
    respx.post("https://api.firecrawl.dev/v2/search").mock(return_value=httpx.Response(402, text="Quota exceeded"))
    with pytest.raises(FirecrawlError) as exc_info:
        client.search("test query")
    assert exc_info.value.status_code == 402

    # 408 Timeout
    respx.post("https://api.firecrawl.dev/v2/search").mock(side_effect=httpx.TimeoutException("timed out"))
    with pytest.raises(FirecrawlError) as exc_info:
        client.search("test query")
    assert exc_info.value.status_code == 408

    # Missing API key
    no_key_client = FirecrawlClient(api_key="")
    with pytest.raises(FirecrawlError) as exc_info:
        no_key_client.search("test query")
    assert "FIRECRAWL_API_KEY is not configured" in str(exc_info.value)


def test_firecrawl_provider_config_validation():
    provider = FirecrawlProvider()

    # Valid config
    provider.api_key = "valid_key"
    settings.firecrawl_enabled = True
    provider.validate_config()

    # Missing key
    provider.api_key = ""
    with pytest.raises(ProviderConfigurationError):
        provider.validate_config()

    # Disabled
    provider.api_key = "valid_key"
    settings.firecrawl_enabled = False
    with pytest.raises(ProviderConfigurationError):
        provider.validate_config()


@respx.mock
def test_firecrawl_provider_search_jobs_normalization():
    mock_resp = {
        "success": True,
        "data": [
            {
                "url": "https://techcorp.com/careers/py-dev-123",
                "title": "Junior Python Developer at TechCorp",
                "description": "Great entry level role for freshers."
            },
            {
                "url": "https://startup.io/jobs/456",
                "title": "Backend Engineer | StartupIO",
                "description": "Django and FastAPI backend work."
            },
            {
                # Missing URL -> should be skipped
                "url": "",
                "title": "Invalid Job",
                "description": "No link"
            },
            {
                # Missing title -> should be skipped
                "url": "https://example.com/no-title",
                "title": "",
                "description": "No title"
            }
        ]
    }
    respx.post("https://api.firecrawl.dev/v2/search").mock(return_value=httpx.Response(200, json=mock_resp))

    provider = FirecrawlProvider()
    query = JobSearchQuery(keywords="Python Developer", location="Bengaluru", page=1, page_size=10)

    jobs = provider.search_jobs(query)
    assert len(jobs) == 2

    job1 = jobs[0]
    assert job1.source == "firecrawl"
    assert job1.title == "Junior Python Developer"
    assert job1.company == "TechCorp"
    assert job1.location == "Bengaluru"
    assert job1.description_is_snippet is True
    assert job1.description == "Great entry level role for freshers."
    assert str(job1.url) == "https://techcorp.com/careers/py-dev-123"
    assert job1.source_job_id.startswith("fc_")

    job2 = jobs[1]
    assert job2.source == "firecrawl"
    assert job2.title == "Backend Engineer"
    assert job2.company == "StartupIO"


@respx.mock
def test_firecrawl_provider_error_mapping():
    provider = FirecrawlProvider()
    query = JobSearchQuery(keywords="Python", location="Bengaluru")

    # 408 mapped to ProviderTimeout
    respx.post("https://api.firecrawl.dev/v2/search").mock(side_effect=httpx.TimeoutException("timeout"))
    with pytest.raises(ProviderTimeout):
        provider.search_jobs(query)

    # 500 mapped to ProviderHTTPError
    respx.post("https://api.firecrawl.dev/v2/search").mock(return_value=httpx.Response(500, text="Server error"))
    with pytest.raises(ProviderHTTPError):
        provider.search_jobs(query)

    # Network error mapped to ProviderNetworkError
    respx.post("https://api.firecrawl.dev/v2/search").mock(side_effect=httpx.ConnectError("connect failed"))
    with pytest.raises(ProviderNetworkError):
        provider.search_jobs(query)

    # Malformed data mapped to ProviderPayloadError
    respx.post("https://api.firecrawl.dev/v2/search").mock(return_value=httpx.Response(200, json={"success": True, "data": "not a list"}))
    with pytest.raises(ProviderPayloadError):
        provider.search_jobs(query)


@respx.mock
def test_run_ingestion_with_firecrawl_quota_success():
    db = SessionLocal()
    try:
        mock_resp = {
            "success": True,
            "data": [
                {
                    "url": "https://example.com/firecrawl_test_job_1",
                    "title": "Software Engineer - InnovateLab",
                    "description": "Fresher role in Bangalore."
                }
            ]
        }
        respx.post("https://api.firecrawl.dev/v2/search").mock(return_value=httpx.Response(200, json=mock_resp))

        provider = FirecrawlProvider()
        query = JobSearchQuery(keywords="Software Engineer", location="Bangalore", page=1, page_size=10)

        result, job_ids = run_ingestion(db, "firecrawl", provider, query)

        assert result.fetched == 1
        assert result.created == 1
        assert len(job_ids) == 1

        # Check that 2 credits were reserved in provider_usage
        state = get_firecrawl_budget_state(db)
        assert state["firecrawl_discovery_used"] == 2
        assert state["firecrawl_monthly_used"] == 2

        # Check job persisted with snippet and enqueued in enrichment
        persisted_job = db.query(JobModel).filter_by(id=job_ids[0]).first()
        assert persisted_job.source == "firecrawl"
        assert persisted_job.company == "InnovateLab"
        assert persisted_job.description_is_snippet is True

        enrichment_entry = db.query(JobEnrichmentModel).filter_by(job_id=job_ids[0]).first()
        assert enrichment_entry is not None
        assert enrichment_entry.status == "pending"
    finally:
        db.close()


def test_run_ingestion_with_firecrawl_quota_denial():
    db = SessionLocal()
    try:
        today = datetime.now(timezone.utc).date()
        # Seed 900 credits used in current month
        db.execute(text("""
            INSERT INTO provider_usage (provider_name, usage_date, request_count)
            VALUES ('firecrawl', :d, 900)
        """), {"d": today})
        db.commit()

        mock_provider = MagicMock(spec=JobProvider)
        query = JobSearchQuery(keywords="Software Engineer", location="Bangalore", page=1, page_size=10)

        with pytest.raises(RateLimitExceeded):
            run_ingestion(db, "firecrawl", mock_provider, query)

        # Provider must NOT have been called
        mock_provider.search_jobs.assert_not_called()
    finally:
        db.close()


def test_cross_provider_deduplication_firecrawl_with_adzuna_and_jooble():
    db = SessionLocal()
    try:
        now_utc = datetime.now(timezone.utc)
        shared_url = "https://example.com/firecrawl_test_cross_provider_job"

        # 1. First discovered by Adzuna
        adzuna_job = Job(
            title="Python Developer",
            company="MetaCorp",
            source="adzuna",
            source_job_id="adz_12345",
            discovered_at=now_utc,
            location="Bengaluru",
            description="Adzuna snippet",
            description_is_snippet=True,
            url=shared_url,
        )
        saved_adzuna, created1 = save_job(db, adzuna_job)
        db.commit()
        assert created1 is True

        # 2. Firecrawl discovers the exact same job
        firecrawl_job = Job(
            title="Python Developer",
            company="MetaCorp",
            source="firecrawl",
            source_job_id="fc_98765",
            discovered_at=now_utc,
            location="Bengaluru",
            description="Firecrawl snippet description",
            description_is_snippet=True,
            url=shared_url,
        )
        saved_fc, created2 = save_job(db, firecrawl_job)
        db.commit()

        # Deduplication must identify it as duplicate
        assert created2 is False
        assert saved_fc.id == saved_adzuna.id

        # Both sources must be linked in job_sources
        sources = db.query(JobSourceModel).filter_by(job_id=saved_adzuna.id).all()
        source_names = {s.source for s in sources}
        assert "adzuna" in source_names
        assert "firecrawl" in source_names
    finally:
        db.close()


def test_fresher_eligibility_and_matching_are_provider_agnostic():
    now_utc = datetime.now(timezone.utc)
    job = Job(
        title="Junior Python Developer",
        company="StartupXYZ",
        source="firecrawl",
        source_job_id="fc_elig_1",
        discovered_at=now_utc,
        location="Bengaluru",
        description="We welcome freshers and 2025 graduates with 0-1 years experience.",
        description_is_snippet=True,
        url="https://example.com/firecrawl_test_elig",
    )

    # 1. Eligibility evaluation (provider-agnostic)
    eligible = is_fresher_eligible(job.title, job.description or "", is_snippet=job.description_is_snippet)
    assert eligible is True

    # 2. Recommendation matching evaluation (provider-agnostic)
    job_resp = JobResponse(
        id=1,
        title=job.title,
        company=job.company,
        source=job.source,
        location=job.location,
        description=job.description,
        description_is_snippet=job.description_is_snippet,
        discovered_at=job.discovered_at,
        url=str(job.url),
    )
    prefs = RecommendationPreferences(
        preferred_roles="Python Developer",
        skills="Python",
        preferred_locations="Bengaluru",
    )
    match_res = calculate_match(job_resp, prefs)
    assert match_res.score > 0
    assert any(r.code == "ROLE_MATCH" for r in match_res.reasons)


def test_provider_package_exports_firecrawl_provider():
    import app.providers as providers
    assert "FirecrawlProvider" in providers.__all__
    assert hasattr(providers, "FirecrawlProvider")
    assert providers.FirecrawlProvider is FirecrawlProvider


def test_firecrawl_title_company_extraction_realistic_delimiters():
    from app.providers.firecrawl import _extract_company_from_title

    # 1. Location delimiter must NOT corrupt company
    title, company = _extract_company_from_title("Junior Python Developer - Bengaluru")
    assert company == "Unknown"

    # 2. Tech stack delimiter must NOT corrupt company
    title, company = _extract_company_from_title("Frontend Engineer - React / TypeScript")
    assert company == "Unknown"

    # 3. Workplace model must NOT corrupt company
    title, company = _extract_company_from_title("Backend Engineer - Remote")
    assert company == "Unknown"

    # 4. Salary/urgency delimiter must NOT corrupt company
    title, company = _extract_company_from_title("Data Analyst - 12 LPA - Urgent")
    assert company == "Unknown"

    # 5. Preposition "at" with location suffix
    title, company = _extract_company_from_title("Software Engineer at InnovateLab - Bengaluru")
    assert title == "Software Engineer"
    assert company == "InnovateLab"

    # 6. Preposition "@" with pipe delimiter
    title, company = _extract_company_from_title("Data Scientist @ Microsoft | Redmond, WA")
    assert title == "Data Scientist"
    assert company == "Microsoft"

    # 7. "Company - Role" pattern
    title, company = _extract_company_from_title("StartupIO - Backend Engineer")
    assert title == "Backend Engineer"
    assert company == "StartupIO"

    # 8. Ambiguous title fails closed to Unknown
    title, company = _extract_company_from_title("Hiring Fresher Candidates")
    assert company == "Unknown"


def test_firecrawl_discovery_routing_isolation_and_enablement(monkeypatch):
    from app.services.provider_router import route_provider, ProviderCapacity
    from unittest.mock import MagicMock

    db_mock = MagicMock()

    # Case A: firecrawl_discovery_enabled = False (default) -> Never routed
    settings.firecrawl_discovery_enabled = False
    assert is_provider_enabled(ProviderName.FIRECRAWL) is False

    # Mock capacities: Adzuna=0 (exhausted), Jooble=0 (exhausted), Firecrawl=10 (available)
    def mock_capacity(db, prov_name, ref):
        rem = 0 if prov_name in (ProviderName.ADZUNA, ProviderName.JOOBLE) else 10
        return ProviderCapacity(prov_name, rem, rem, rem, rem, rem)

    monkeypatch.setattr("app.services.provider_router.get_provider_capacity", mock_capacity)

    from app.services.provider_router import ProviderQuotaExhausted
    with pytest.raises(ProviderQuotaExhausted):
        route_provider(db_mock)

    # Case B: firecrawl_discovery_enabled = True -> Generic Search Cycle STILL never selects Firecrawl!
    # Because Firecrawl is intentionally excluded from continuous search cycle priority to protect the pool.
    settings.firecrawl_discovery_enabled = True
    assert is_provider_enabled(ProviderName.FIRECRAWL) is True

    # When Adzuna and Jooble are exhausted, route_provider still raises ProviderQuotaExhausted
    with pytest.raises(ProviderQuotaExhausted):
        route_provider(db_mock)

    # Dedicated Phase 4 workflow invokes Firecrawl directly without generic router
    fc_provider = create_provider(ProviderName.FIRECRAWL)
    assert isinstance(fc_provider, FirecrawlProvider)


def test_canonical_deduplication_unknown_company_safe():
    db = SessionLocal()
    try:
        now_utc = datetime.now(timezone.utc)
        # Job 1 with Unknown company
        job1 = Job(
            title="Junior Python Developer",
            company="Unknown",
            source="firecrawl",
            source_job_id="fc_unk_1",
            discovered_at=now_utc,
            location="Bengaluru",
            description="Role in Bengaluru.",
            description_is_snippet=True,
            url="https://example.com/firecrawl_test_unk_1",
        )
        saved1, created1 = save_job(db, job1)
        db.commit()
        assert created1 is True

        # Job 2 with identical direct URL -> must deduplicate correctly via URL hash
        job2 = Job(
            title="Junior Python Developer - Bengaluru",
            company="Unknown",
            source="firecrawl",
            source_job_id="fc_unk_2",
            discovered_at=now_utc,
            location="Bengaluru",
            description="Role in Bengaluru.",
            description_is_snippet=True,
            url="https://example.com/firecrawl_test_unk_1",
        )
        saved2, created2 = save_job(db, job2)
        db.commit()
        assert created2 is False
        assert saved2.id == saved1.id

        # Job 3 with distinct direct URL -> must NOT collide on Unknown company
        job3 = Job(
            title="Junior Python Developer",
            company="Unknown",
            source="firecrawl",
            source_job_id="fc_unk_3",
            discovered_at=now_utc,
            location="Bengaluru",
            description="Different job in Bengaluru.",
            description_is_snippet=True,
            url="https://example.com/firecrawl_test_unk_3",
        )
        saved3, created3 = save_job(db, job3)
        db.commit()
        assert created3 is True
        assert saved3.id != saved1.id
    finally:
        db.close()
