import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy import text
from unittest.mock import patch, MagicMock

from app.main import app
from app.core.config import settings
from app.db.database import SessionLocal
from app.db.models.user import User
from app.db.models.job import JobModel
from app.db.models.job_enrichment import JobEnrichmentModel
from app.db.models.recommendation_history import RecommendationHistoryModel
from app.db.models.notification_delivery import NotificationDeliveryModel
from app.db.models.search_execution import SearchExecutionModel

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_funnel_db():
    def _cleanup():
        db = SessionLocal()
        try:
            db.execute(text("DELETE FROM recommendation_history WHERE job_id IN (SELECT id FROM jobs WHERE source_job_id LIKE 'fc_funnel_test_%')"))
            db.execute(text("DELETE FROM notification_deliveries WHERE delivery_id = 'deliv_funnel_123'"))
            db.execute(text("DELETE FROM job_enrichments WHERE url LIKE '%startup1.com%' OR url LIKE '%startup2.com%' OR url LIKE '%exec1.com%'"))
            db.execute(text("DELETE FROM jobs WHERE source_job_id LIKE 'fc_funnel_test_%'"))
            db.execute(text("DELETE FROM search_execution WHERE candidate_id LIKE 'fc_disc_test_funnel_%'"))
            db.execute(text("DELETE FROM users WHERE email = 'test_funnel_user@example.com'"))
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()

    _cleanup()
    yield
    _cleanup()


def test_funnel_endpoint_requires_auth():
    resp = client.get("/ingestion/internal/firecrawl-discovery/funnel")
    assert resp.status_code in (401, 403)


def test_funnel_endpoint_metrics():
    db = SessionLocal()
    headers = {"X-API-Key": settings.api_secret_key}

    user = User(email="test_funnel_user@example.com", password_hash="pw", is_active=True)
    db.add(user)
    db.commit()

    delivery = NotificationDeliveryModel(
        delivery_id="deliv_funnel_123",
        user_id=user.id,
        claimed_at=datetime.now(timezone.utc),
    )
    db.add(delivery)
    db.commit()

    now = datetime.now(timezone.utc)
    job1 = JobModel(
        title="Backend Developer",
        company="Startup 1",
        source="firecrawl",
        source_job_id="fc_funnel_test_1",
        discovered_at=now,
        description="Snippet 1",
        description_is_snippet=True,
        url="https://startup1.com/jobs/123",
        canonical_hash="hash_funnel_1",
    )
    job2 = JobModel(
        title="Python Engineer",
        company="Startup 2",
        source="firecrawl",
        source_job_id="fc_funnel_test_2",
        discovered_at=now,
        description="Full desc 2",
        description_is_snippet=False,
        url="https://startup2.com/jobs/456",
        canonical_hash="hash_funnel_2",
    )
    db.add_all([job1, job2])
    db.commit()

    enrich1 = JobEnrichmentModel(
        job_id=job1.id,
        status="unsupported",
        error_reason="rejected_listing_content: aggregator_marker",
        result_telemetry={"validation": "rejected_listing", "reason": "aggregator_marker"},
        url=job1.url,
    )
    enrich2 = JobEnrichmentModel(
        job_id=job2.id,
        status="success",
        result_telemetry={"validation": "validated_posting", "text_length": 500},
        url=job2.url,
    )
    db.add_all([enrich1, enrich2])

    rec = RecommendationHistoryModel(
        user_id=user.id,
        job_id=job2.id,
        score=85,
        delivery_id="deliv_funnel_123",
    )
    db.add(rec)
    db.commit()
    db.close()

    resp = client.get("/ingestion/internal/firecrawl-discovery/funnel", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    assert data["jobs_ingested"] >= 2
    assert data["jobs_snippet"] >= 1
    assert data["jobs_full_description"] >= 1
    assert data["enrichments_success"] >= 1
    assert data["enrichments_unsupported"] >= 1
    assert data["listings_rejected_at_enrichment"] >= 1
    assert data["recommendations_total"] >= 1
    assert data["recommendations_delivered"] >= 1


def test_firecrawl_telemetry_includes_stage_counters():
    headers = {"X-API-Key": settings.api_secret_key}
    resp = client.get("/ingestion/internal/firecrawl-telemetry", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "stage_counters" in data
    assert data["stage_counters"] is not None
    # Semantically truthful: unproven global stages must be null, not invented
    assert data["stage_counters"]["candidates_found"] is None
    assert data["stage_counters"]["validated_individual"] is None
    assert data["stage_counters"]["enriched"] is None
    assert data["stage_counters"]["eligible"] is None
    assert data["stage_counters"]["recommended"] is None


def test_funnel_endpoint_by_execution_id():
    import json
    db = SessionLocal()
    headers = {"X-API-Key": settings.api_secret_key}

    now = datetime.now(timezone.utc)
    execution = SearchExecutionModel(
        candidate_id="fc_disc_test_funnel_exec_1",
        status="succeeded",
        provider_name="firecrawl",
        query_variant="Python Developer fresher",
        retrieval_location="Bengaluru",
        selected_at=now,
        started_at=now,
        completed_at=now,
        jobs_fetched=2,
        jobs_created=2,
        jobs_duplicates=0,
        jobs_invalid=0,
        jobs_fresher_eligible=1,
        recommendations_created=1,
        error_message=json.dumps({"rejections_by_reason": {"naukri_listing": 3, "indeed_listing": 2}}),
    )
    db.add(execution)
    db.commit()

    job = JobModel(
        title="Python Developer",
        company="ExecCo",
        source="firecrawl",
        source_job_id="fc_funnel_test_exec_job",
        discovered_at=now,
        description="Full job description",
        description_is_snippet=False,
        url="https://exec1.com/jobs/1",
        canonical_hash="hash_funnel_exec_1",
    )
    db.add(job)
    db.commit()

    enrich = JobEnrichmentModel(
        job_id=job.id,
        status="success",
        source_execution_id=execution.id,
        result_telemetry={"validation": "validated_posting", "text_length": 300},
        url=job.url,
    )
    db.add(enrich)
    db.commit()

    user = User(email="test_funnel_user@example.com", password_hash="pw", is_active=True)
    existing_user = db.query(User).filter(User.email == "test_funnel_user@example.com").first()
    if not existing_user:
        db.add(user)
        db.commit()
        uid = user.id
    else:
        uid = existing_user.id

    rec = RecommendationHistoryModel(
        user_id=uid,
        job_id=job.id,
        score=90,
    )
    db.add(rec)
    db.commit()
    exec_id = execution.id
    db.close()

    resp = client.get(f"/ingestion/internal/firecrawl-discovery/funnel?execution_id={exec_id}", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    assert data["execution_id"] == exec_id
    assert data["jobs_ingested"] == 1
    assert data["jobs_full_description"] == 1
    assert data["enrichments_success"] == 1
    assert data["fresher_eligible"] == 1
    assert data["recommendations_total"] == 1
    assert "rejections_by_reason" in data
    assert data["rejections_by_reason"].get("naukri_listing") == 3
    assert data["rejections_by_reason"].get("indeed_listing") == 2


def test_funnel_endpoint_nonexistent_execution_id_returns_404():
    headers = {"X-API-Key": settings.api_secret_key}
    resp = client.get("/ingestion/internal/firecrawl-discovery/funnel?execution_id=999999999", headers=headers)
    assert resp.status_code == 404
    assert "Execution not found" in resp.json()["detail"]


def test_concurrent_discovery_runs_execution_identity_collision_safety():
    """
    Prove concurrent discovery runs cannot collide on execution identity (candidate_id).
    Previously, using integer timestamp second caused two concurrent runs to generate
    identical candidate_id and trigger PostgreSQL uq_active_claim unique constraint violation.
    """
    import concurrent.futures
    from unittest.mock import MagicMock, patch
    from app.services.firecrawl_discovery import execute_discovery_run, FirecrawlDiscoveryPlan, DiscoveryQuery
    from app.db.models.search_execution import SearchExecutionModel

    mock_provider = MagicMock()
    mock_provider.search_jobs.return_value = []
    mock_provider.last_search_telemetry = {
        "candidates_found": 0,
        "candidates_accepted": 0,
        "candidates_rejected": 0,
        "rejections_by_reason": {},
    }

    plan = FirecrawlDiscoveryPlan(
        enabled=True,
        max_queries=1,
        max_results_per_query=10,
        max_credits_per_run=10,
        queries=[
            DiscoveryQuery(
                keywords="Python Developer",
                location="Bengaluru",
                page_size=10,
                estimated_credits=2,
            )
        ],
    )

    def run_worker():
        db = SessionLocal()
        try:
            with patch("app.services.firecrawl_discovery.create_provider", return_value=mock_provider):
                return execute_discovery_run(db, plan=plan, force=True)
        finally:
            db.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(run_worker)
        f2 = executor.submit(run_worker)
        telem1 = f1.result()
        telem2 = f2.result()

    assert telem1.status == "completed"
    assert telem2.status == "completed"
    assert len(telem1.execution_ids) == 1
    assert len(telem2.execution_ids) == 1
    assert telem1.execution_ids[0] != telem2.execution_ids[0]
    assert telem1.run_id != telem2.run_id

    db = SessionLocal()
    try:
        e1 = db.query(SearchExecutionModel).filter(SearchExecutionModel.id == telem1.execution_ids[0]).first()
        e2 = db.query(SearchExecutionModel).filter(SearchExecutionModel.id == telem2.execution_ids[0]).first()
        assert e1 is not None and e2 is not None
        assert e1.candidate_id != e2.candidate_id
        assert e1.cycle_id == telem1.run_id
        assert e2.cycle_id == telem2.run_id
        assert e1.candidate_id.startswith(f"fc_disc_{telem1.run_id}_1_")
        assert e2.candidate_id.startswith(f"fc_disc_{telem2.run_id}_1_")
    finally:
        db.query(SearchExecutionModel).filter(SearchExecutionModel.id.in_(telem1.execution_ids + telem2.execution_ids)).delete()
        db.execute(text("DELETE FROM provider_usage WHERE provider_name IN ('firecrawl', 'firecrawl_monthly')"))
        db.commit()
        db.close()


def test_deterministic_execution_identity_when_run_id_supplied():
    """
    Prove that supplying run_id generates deterministic collision-safe candidate_id and sets cycle_id.
    """
    from unittest.mock import MagicMock, patch
    from app.services.firecrawl_discovery import execute_discovery_run, FirecrawlDiscoveryPlan, DiscoveryQuery
    from app.db.models.search_execution import SearchExecutionModel

    mock_provider = MagicMock()
    mock_provider.search_jobs.return_value = []
    mock_provider.last_search_telemetry = {}

    plan = FirecrawlDiscoveryPlan(
        enabled=True,
        max_queries=1,
        max_results_per_query=10,
        max_credits_per_run=10,
        queries=[
            DiscoveryQuery(
                keywords="Backend Engineer",
                location="Bengaluru",
                page_size=10,
                estimated_credits=2,
            )
        ],
    )

    db = SessionLocal()
    telem = None
    try:
        with patch("app.services.firecrawl_discovery.create_provider", return_value=mock_provider):
            telem = execute_discovery_run(db, plan=plan, force=True, run_id="fc_run_fixed_test_123")
        assert telem.run_id == "fc_run_fixed_test_123"
        e = db.query(SearchExecutionModel).filter(SearchExecutionModel.id == telem.execution_ids[0]).first()
        assert e is not None
        assert e.cycle_id == "fc_run_fixed_test_123"
        assert e.candidate_id.startswith("fc_disc_fc_run_fixed_test_123_1_")
    finally:
        if telem and telem.execution_ids:
            db.query(SearchExecutionModel).filter(SearchExecutionModel.id.in_(telem.execution_ids)).delete()
        db.execute(text("DELETE FROM provider_usage WHERE provider_name IN ('firecrawl', 'firecrawl_monthly')"))
        db.commit()
        db.close()
