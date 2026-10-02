import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from app.core.config import settings
from app.db.database import SessionLocal
from app.db.models.user import User
from app.db.models.user_search import UserSearch
from app.db.models.user_profile import UserProfile
from app.models.job import Job
from app.providers.types import ProviderName
from app.providers.exceptions import ProviderError
from app.schemas.job_search import JobSearchQuery, IngestionResult
from app.services.firecrawl_discovery import (
    DiscoveryQuery,
    FirecrawlDiscoveryPlan,
    FirecrawlDiscoveryTelemetry,
    plan_discovery_queries,
    execute_discovery_run,
    _format_discovery_keywords,
)
from app.services.ingestion import RateLimitExceeded

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_discovery_db():
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
    db.execute(text("DELETE FROM user_searches WHERE query LIKE '%TestDiscovery%'"))
    db.execute(text("DELETE FROM job_sources WHERE source = 'firecrawl'"))
    db.execute(text("DELETE FROM jobs WHERE source = 'firecrawl'"))
    db.commit()
    db.close()

    try:
        yield
    finally:
        settings.firecrawl_api_key = orig_key
        settings.firecrawl_enabled = orig_enabled
        settings.firecrawl_discovery_enabled = orig_disc_enabled
        settings.firecrawl_monthly_automation_cap = orig_cap
        settings.firecrawl_monthly_budget = orig_budget

        db = SessionLocal()
        db.execute(text("DELETE FROM provider_usage WHERE provider_name IN ('firecrawl', 'firecrawl_monthly')"))
        db.execute(text("DELETE FROM user_searches WHERE query LIKE '%TestDiscovery%'"))
        db.execute(text("DELETE FROM job_sources WHERE source = 'firecrawl'"))
        db.execute(text("DELETE FROM jobs WHERE source = 'firecrawl'"))
        db.commit()
        db.close()


def test_format_discovery_keywords():
    assert _format_discovery_keywords("Python Developer") == "Python Developer fresher"
    assert _format_discovery_keywords("Django Developer fresher") == "Django Developer fresher"
    assert _format_discovery_keywords("Junior React Engineer") == "Junior React Engineer"
    assert _format_discovery_keywords("Backend Developer Entry Level") == "Backend Developer Entry Level"


def test_plan_discovery_queries_disabled():
    settings.firecrawl_discovery_enabled = False
    db = SessionLocal()
    try:
        plan = plan_discovery_queries(db, force=False)
        assert plan.enabled is False
        assert len(plan.queries) == 0
        assert "disabled" in (plan.reason or "").lower()
    finally:
        db.close()


def test_plan_discovery_queries_from_user_search():
    db = SessionLocal()
    try:
        # Create active user if none exists
        user = db.query(User).filter(User.is_active == True).first()
        if not user:
            user = User(email="test_discovery@example.com", hashed_password="pw", is_active=True)
            db.add(user)
            db.commit()

        us = UserSearch(
            user_id=user.id,
            query="TestDiscovery Python",
            location="Bengaluru",
            enabled=True
        )
        db.add(us)
        db.commit()

        plan = plan_discovery_queries(db, force=True)
        assert plan.enabled is True
        assert len(plan.queries) <= settings.firecrawl_discovery_max_queries_per_run
        assert any("TestDiscovery Python" in q.keywords for q in plan.queries)
        assert all(q.page_size == settings.firecrawl_discovery_max_results_per_query for q in plan.queries)
        assert all(q.estimated_credits == 2 for q in plan.queries)
    finally:
        db.close()


def test_plan_discovery_queries_taxonomy_fallback():
    db = SessionLocal()
    try:
        # Ensure no user searches match
        db.execute(text("DELETE FROM user_searches"))
        db.commit()

        plan = plan_discovery_queries(db, force=True)
        assert plan.enabled is True
        assert len(plan.queries) > 0
        assert len(plan.queries) <= settings.firecrawl_discovery_max_queries_per_run
        assert all(q.page_size == 10 for q in plan.queries)
        assert all(q.estimated_credits == 2 for q in plan.queries)
    finally:
        db.close()


def test_plan_discovery_queries_budget_exhausted():
    db = SessionLocal()
    try:
        # Simulate budget exhausted (900 credits used)
        today = settings.firecrawl_monthly_automation_cap
        db.execute(
            text("INSERT INTO provider_usage (provider_name, usage_date, request_count) VALUES ('firecrawl', CURRENT_DATE, :c)"),
            {"c": today}
        )
        db.commit()

        plan = plan_discovery_queries(db, force=True)
        assert len(plan.queries) == 0
        assert "exhausted" in (plan.reason or "").lower()
    finally:
        db.close()


def test_ingest_firecrawl_endpoint_auth():
    # 401 without auth
    res = client.post("/ingestion/firecrawl", json={"keywords": "Python", "location": "Bengaluru"})
    assert res.status_code == 401

    # 401 with wrong key
    res = client.post("/ingestion/firecrawl", headers={"X-Api-Key": "wrong"}, json={"keywords": "Python", "location": "Bengaluru"})
    assert res.status_code == 401


def test_ingest_firecrawl_endpoint_success():
    headers = {"X-Api-Key": settings.api_secret_key}
    mock_job = Job(
        title="Python Developer",
        company="Acme Corp",
        source="firecrawl",
        source_job_id="fc_test_001",
        url="https://example.com/job/fc_001",
        location="Bengaluru",
        description="Python backend developer wanted",
        description_is_snippet=True,
        discovered_at=datetime.now(timezone.utc),
    )

    with patch("app.api.endpoints.ingestion.create_provider") as mock_create:
        mock_provider = MagicMock()
        mock_provider.search_jobs.return_value = [mock_job]
        mock_create.return_value = mock_provider

        res = client.post(
            "/ingestion/firecrawl",
            headers=headers,
            json={"keywords": "Python", "location": "Bengaluru", "page_size": 10}
        )
        assert res.status_code == 200
        data = res.json()
        assert data["provider"] == "firecrawl"
        assert data["fetched"] == 1
        assert data["created"] == 1


def test_ingest_firecrawl_endpoint_quota_exhausted():
    headers = {"X-Api-Key": settings.api_secret_key}
    db = SessionLocal()
    try:
        # Set usage to 900
        db.execute(
            text("INSERT INTO provider_usage (provider_name, usage_date, request_count) VALUES ('firecrawl', CURRENT_DATE, 900)")
        )
        db.commit()
    finally:
        db.close()

    res = client.post(
        "/ingestion/firecrawl",
        headers=headers,
        json={"keywords": "Python", "location": "Bengaluru", "page_size": 10}
    )
    assert res.status_code == 429
    assert "quota exceeded" in res.json()["detail"].lower()


def test_ingest_firecrawl_endpoint_provider_failure_returns_502():
    """Prove that a Firecrawl result with failed > 0 properly returns HTTP 502, not 500."""
    headers = {"X-Api-Key": settings.api_secret_key}

    with patch("app.api.endpoints.ingestion.create_provider") as mock_create:
        mock_provider = MagicMock()
        mock_provider.search_jobs.side_effect = ProviderError("External provider failure")
        mock_create.return_value = mock_provider

        res = client.post(
            "/ingestion/firecrawl",
            headers=headers,
            json={"keywords": "Python", "location": "Bengaluru", "page_size": 10}
        )
        assert res.status_code == 502
        assert "firecrawl provider failed" in res.json()["detail"].lower()


def test_ingest_firecrawl_endpoint_database_unavailable_returns_503():
    """Prove that DatabaseUnavailable in Firecrawl ingestion returns HTTP 503."""
    headers = {"X-Api-Key": settings.api_secret_key}

    with patch("app.api.endpoints.ingestion.create_provider"):
        with patch("app.api.endpoints.ingestion.run_ingestion") as mock_run:
            from app.services.ingestion import DatabaseUnavailable
            mock_run.side_effect = DatabaseUnavailable("Database offline")

            res = client.post(
                "/ingestion/firecrawl",
                headers=headers,
                json={"keywords": "Python", "location": "Bengaluru", "page_size": 10}
            )
            assert res.status_code == 503
            assert "database temporarily unavailable" in res.json()["detail"].lower()


def test_ingest_firecrawl_endpoint_provider_configuration_error_returns_500():
    """Prove that ProviderConfigurationError in Firecrawl ingestion returns HTTP 500."""
    headers = {"X-Api-Key": settings.api_secret_key}

    with patch("app.api.endpoints.ingestion.create_provider") as mock_create:
        from app.providers.exceptions import ProviderConfigurationError
        mock_create.side_effect = ProviderConfigurationError("Missing API key")

        res = client.post(
            "/ingestion/firecrawl",
            headers=headers,
            json={"keywords": "Python", "location": "Bengaluru", "page_size": 10}
        )
        assert res.status_code == 500
        assert "provider configuration error" in res.json()["detail"].lower()


def test_internal_plan_endpoint():
    headers = {"X-Api-Key": settings.api_secret_key}
    res = client.post("/ingestion/internal/firecrawl-discovery/plan", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "enabled" in data
    assert "queries" in data
    assert "max_queries" in data


def test_internal_run_endpoint_disabled():
    settings.firecrawl_discovery_enabled = False
    headers = {"X-Api-Key": settings.api_secret_key}
    res = client.post("/ingestion/internal/firecrawl-discovery/run", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "disabled"
    assert data["query_count"] == 0


def test_internal_run_endpoint_success():
    headers = {"X-Api-Key": settings.api_secret_key}
    mock_jobs = [
        Job(
            title="FastAPI Developer",
            company="Startup Inc",
            source="firecrawl",
            source_job_id="fc_run_001",
            url="https://example.com/job/fc_run_001",
            location="Bengaluru",
            description="FastAPI developer needed",
            description_is_snippet=True,
            discovered_at=datetime.now(timezone.utc),
        )
    ]

    with patch("app.services.firecrawl_discovery.create_provider") as mock_create:
        mock_provider = MagicMock()
        mock_provider.search_jobs.return_value = mock_jobs
        mock_create.return_value = mock_provider

        plan = FirecrawlDiscoveryPlan(
            enabled=True,
            max_queries=1,
            max_results_per_query=10,
            max_credits_per_run=20,
            queries=[
                DiscoveryQuery(
                    keywords="FastAPI Developer fresher",
                    location="Bengaluru",
                    page_size=10,
                    estimated_credits=2
                )
            ]
        )

        res = client.post(
            "/ingestion/internal/firecrawl-discovery/run",
            headers=headers,
            json=plan.model_dump()
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "completed"
        assert data["query_count"] == 1
        assert data["attempted_queries"] == 1
        assert data["successful_queries"] == 1
        assert data["candidates_found"] == 1
        assert data["candidates_accepted"] == 1
        assert data["credits_consumed"] == 2


def test_internal_run_partial_query_failure_continuation():
    headers = {"X-Api-Key": settings.api_secret_key}
    mock_jobs = [
        Job(
            title="Django Developer",
            company="Acme Corp",
            source="firecrawl",
            source_job_id="fc_run_002",
            url="https://example.com/job/fc_run_002",
            location="Bengaluru",
            description="Django developer",
            description_is_snippet=True,
            discovered_at=datetime.now(timezone.utc),
        )
    ]

    with patch("app.services.firecrawl_discovery.create_provider") as mock_create:
        mock_provider = MagicMock()
        # Query 1 fails with ProviderError, Query 2 succeeds
        mock_provider.search_jobs.side_effect = [
            ProviderError("Firecrawl HTTP error 502"),
            mock_jobs
        ]
        mock_create.return_value = mock_provider

        plan = FirecrawlDiscoveryPlan(
            enabled=True,
            max_queries=2,
            max_results_per_query=10,
            max_credits_per_run=20,
            queries=[
                DiscoveryQuery(keywords="Fail Query fresher", location="Bengaluru", page_size=10, estimated_credits=2),
                DiscoveryQuery(keywords="Success Query fresher", location="Bengaluru", page_size=10, estimated_credits=2),
            ]
        )

        res = client.post(
            "/ingestion/internal/firecrawl-discovery/run",
            headers=headers,
            json=plan.model_dump()
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "completed"
        assert data["query_count"] == 2
        assert data["attempted_queries"] == 2
        assert data["successful_queries"] == 1
        assert data["ingestion"]["failed"] == 1
        assert data["candidates_accepted"] == 1
        # In Phase 4.2: Both Query 1 and Query 2 reserved quota in the ledger before provider call!
        assert data["credits_consumed"] == 4
        assert data["credits_reserved"] == 4

        # Verify DB provider_usage exactly matches telemetry
        db = SessionLocal()
        try:
            actual_db = db.execute(text("SELECT request_count FROM provider_usage WHERE provider_name = 'firecrawl' AND usage_date = CURRENT_DATE")).scalar()
            assert actual_db == 4
            assert data["credits_consumed"] == actual_db
        finally:
            db.close()


def test_internal_run_quota_stopped_safely():
    headers = {"X-Api-Key": settings.api_secret_key}

    db = SessionLocal()
    try:
        # Pre-populate usage to 899 credits
        db.execute(
            text("INSERT INTO provider_usage (provider_name, usage_date, request_count) VALUES ('firecrawl', CURRENT_DATE, 899)")
        )
        db.commit()
    finally:
        db.close()

    plan = FirecrawlDiscoveryPlan(
        enabled=True,
        max_queries=2,
        max_results_per_query=10,
        max_credits_per_run=20,
        queries=[
            DiscoveryQuery(keywords="Query 1", location="Bengaluru", page_size=10, estimated_credits=2),
            DiscoveryQuery(keywords="Query 2", location="Bengaluru", page_size=10, estimated_credits=2),
        ]
    )

    # First query needs 2 credits, 899 + 2 = 901 > 900 -> RateLimitExceeded
    res = client.post(
        "/ingestion/internal/firecrawl-discovery/run",
        headers=headers,
        json=plan.model_dump()
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "quota_stopped"
    assert data["successful_queries"] == 0
    assert data["credits_consumed"] == 0
    assert data["credits_reserved"] == 0

    # Ensure DB usage was NOT incremented by the denied query
    db = SessionLocal()
    try:
        actual_db = db.execute(text("SELECT request_count FROM provider_usage WHERE provider_name = 'firecrawl' AND usage_date = CURRENT_DATE")).scalar()
        assert actual_db == 899
    finally:
        db.close()


def test_tampered_plan_page_size_and_falsified_estimated_credits():
    """
    Verify server-authoritative enforcement:
    1. Client tampered page_size=50 is normalized/clamped to max_results_per_query (10).
    2. Client falsified estimated_credits=1 is ignored and recomputed authoritatively as 2.
    3. Client tampered max_queries=10 with 5 queries is capped to settings.firecrawl_discovery_max_queries_per_run (2).
    """
    headers = {"X-Api-Key": settings.api_secret_key}
    mock_jobs = [
        Job(
            title="Python Developer",
            company="Acme Corp",
            source="firecrawl",
            source_job_id="fc_tamper_01",
            url="https://example.com/job/fc_tamper_01",
            location="Bengaluru",
            description="Python developer",
            description_is_snippet=True,
            discovered_at=datetime.now(timezone.utc),
        )
    ]

    with patch("app.services.firecrawl_discovery.create_provider") as mock_create:
        mock_provider = MagicMock()
        mock_provider.search_jobs.return_value = mock_jobs
        mock_create.return_value = mock_provider

        # Client submits tampered plan
        tampered_plan = FirecrawlDiscoveryPlan(
            enabled=True,
            max_queries=10,  # tampered
            max_results_per_query=50,  # tampered
            max_credits_per_run=100,  # tampered
            queries=[
                DiscoveryQuery(keywords=f"Tampered Query {i}", location="Bengaluru", page_size=50, estimated_credits=1)
                for i in range(5)
            ]
        )

        res = client.post(
            "/ingestion/internal/firecrawl-discovery/run",
            headers=headers,
            json=tampered_plan.model_dump()
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "completed"
        # Bounded strictly to settings.firecrawl_discovery_max_queries_per_run (2)
        assert data["query_count"] == 2
        assert data["attempted_queries"] == 2
        assert data["successful_queries"] == 2
        # Page size was clamped to 10 -> each costs calculate_search_credits(10) == 2
        # 2 queries * 2 credits = 4 credits
        assert data["estimated_credits"] == 4
        assert data["credits_consumed"] == 4
        assert data["credits_reserved"] == 4

        # Verify provider.search_jobs was called with normalized page_size=10, not 50
        calls = mock_provider.search_jobs.call_args_list
        assert len(calls) == 2
        for c in calls:
            called_query = c[0][0]
            assert called_query.page_size == settings.firecrawl_discovery_max_results_per_query
            assert called_query.page_size <= 10


def test_provider_failure_after_reservation_aligns_telemetry_with_provider_usage():
    """
    Verify that if a provider fails after reservation, the credits committed in provider_usage
    are reported accurately in telemetry (no contradiction).
    """
    headers = {"X-Api-Key": settings.api_secret_key}

    with patch("app.services.firecrawl_discovery.create_provider") as mock_create:
        mock_provider = MagicMock()
        mock_provider.search_jobs.side_effect = ProviderError("External HTTP 504 Gateway Timeout")
        mock_create.return_value = mock_provider

        plan = FirecrawlDiscoveryPlan(
            enabled=True,
            max_queries=1,
            max_results_per_query=10,
            max_credits_per_run=20,
            queries=[
                DiscoveryQuery(keywords="Timeout Query fresher", location="Bengaluru", page_size=10, estimated_credits=2),
            ]
        )

        res = client.post(
            "/ingestion/internal/firecrawl-discovery/run",
            headers=headers,
            json=plan.model_dump()
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "completed"
        assert data["attempted_queries"] == 1
        assert data["successful_queries"] == 0
        assert data["ingestion"]["failed"] == 1
        # Quota slot was reserved in provider_usage before search_jobs failed!
        assert data["credits_consumed"] == 2
        assert data["credits_reserved"] == 2

        # Verify DB provider_usage matches exactly
        db = SessionLocal()
        try:
            actual_usage = db.execute(text("SELECT request_count FROM provider_usage WHERE provider_name = 'firecrawl' AND usage_date = CURRENT_DATE")).scalar()
            assert actual_usage == 2
            assert data["credits_consumed"] == actual_usage
            assert data["credits_reserved"] == actual_usage
        finally:
            db.close()
