import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from sqlalchemy import text

from app.services.enrichment_worker import EnrichmentWorker
from app.db.models.job import JobModel
from app.db.models.job_enrichment import JobEnrichmentModel
from app.db.models.recommendation_history import RecommendationHistoryModel
from app.db.models.user import User
from app.db.models.user_search import UserSearch
from app.db.models.user_profile import UserProfile
from app.models.job import Job as PydanticJob
from app.db.repository.job_repository import save_job, enqueue_job_enrichment

@pytest.fixture
def mock_ssrf():
    with patch('app.services.enrichment_worker.SSRFClient') as mock:
        yield mock

def test_enrichment_worker_success(db_session, mock_ssrf):
    # Insert job
    job = JobModel(
        title="Software Engineer",
        company="Tech Corp",
        source="test",
        source_job_id="123",
        discovered_at=datetime.now(timezone.utc),
        description="Snippet...",
        description_is_snippet=True,
        url="https://example.com/job"
    )
    db_session.add(job)

    # Insert user and search
    user = User(email="test@example.com", password_hash="dummy")
    db_session.add(user)
    db_session.flush()

    user_search = UserSearch(user_id=user.id, query="Software Engineer", enabled=True)
    db_session.add(user_search)

    user_profile = UserProfile(user_id=user.id, preferred_roles=["Software Engineer"])
    db_session.add(user_profile)

    enrichment = JobEnrichmentModel(
        job_id=job.id,
        status='pending',
        url=job.url
    )
    db_session.add(enrichment)
    db_session.commit()

    # Mock fetch and extract
    mock_instance = mock_ssrf.return_value
    mock_resp = MagicMock()
    mock_resp.text = "<html><body>Some full description text here with more than 200 characters so it passes the length check. " * 5 + "</body></html>"
    mock_instance.fetch.return_value = mock_resp

    with patch('app.services.enrichment_worker.is_fresher_eligible') as mock_eligible, \
         patch('app.services.enrichment_worker.calculate_match') as mock_score:
        mock_eligible.return_value = True
        mock_score_res = MagicMock()
        mock_score_res.score = 75
        mock_score.return_value = mock_score_res

        worker = EnrichmentWorker()
        worker.claim_and_process(db_session)

    db_session.refresh(job)
    db_session.refresh(enrichment)

    assert enrichment.status == 'success'
    assert not job.description_is_snippet
    assert "Some full description text here" in job.description

    # Check recommendation
    rec = db_session.query(RecommendationHistoryModel).filter_by(job_id=job.id).first()
    assert rec is not None
    assert rec.user_id == user.id

def test_enrichment_worker_stale_lease(db_session):
    job = JobModel(
        title="Software Engineer",
        company="Tech Corp",
        source="test",
        source_job_id="456",
        discovered_at=datetime.now(timezone.utc),
        description="Snippet...",
        description_is_snippet=True,
        url="https://example.com/job2"
    )
    db_session.add(job)
    db_session.flush()

    enrichment = JobEnrichmentModel(
        job_id=job.id,
        status='in_progress',
        url=job.url,
        lease_token="expired_token",
        lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=10)
    )
    db_session.add(enrichment)
    db_session.commit()

    worker = EnrichmentWorker()
    worker.complete_success(db_session, job.id, "expired_token", "full text")

    db_session.refresh(enrichment)
    assert enrichment.status == 'in_progress'

def test_enrichment_worker_reclamation(db_session):
    job = JobModel(
        title="Software Engineer",
        company="Tech Corp",
        source="test",
        source_job_id="789",
        discovered_at=datetime.now(timezone.utc),
        description="Snippet...",
        description_is_snippet=True,
        url="https://example.com/job3"
    )
    db_session.add(job)
    db_session.flush()

    enrichment = JobEnrichmentModel(
        job_id=job.id,
        status='in_progress',
        url=job.url,
        lease_token="old_token",
        lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=10)
    )
    db_session.add(enrichment)
    db_session.commit()

    with patch.object(EnrichmentWorker, 'process_job') as mock_process:
        worker = EnrichmentWorker()
        worker.claim_and_process(db_session)

        db_session.refresh(enrichment)
        assert enrichment.lease_token != "old_token"
        assert enrichment.status == 'in_progress'
        mock_process.assert_called_once()

def test_save_job_repeated_call_leaves_single_enrichment(db_session):
    now = datetime.now(timezone.utc)
    job = PydanticJob(
        title="Software Engineer",
        company="Tech Corp",
        source="adzuna",
        source_job_id="dup_test_101",
        discovered_at=now,
        description="Short snippet...",
        description_is_snippet=True,
        url="https://example.com/job"
    )

    # First save
    db_job1, created1 = save_job(db_session, job)
    assert created1 is True

    # Second save of the exact same job
    db_job2, created2 = save_job(db_session, job)
    assert created2 is False
    assert db_job1.id == db_job2.id

    # Verify exactly one enrichment row was created and remains pending
    enrichments = db_session.query(JobEnrichmentModel).filter_by(job_id=db_job1.id).all()
    assert len(enrichments) == 1
    assert enrichments[0].status == 'pending'
    assert enrichments[0].url == "https://example.com/job"

def test_enqueue_job_enrichment_conflict_safe_idempotent(db_session):
    now = datetime.now(timezone.utc)
    job = JobModel(
        title="Data Engineer",
        company="Data Corp",
        source="jooble",
        source_job_id="conflict_test_202",
        discovered_at=now,
        description="Snippet...",
        description_is_snippet=True,
        url="https://example.com/data_job"
    )
    db_session.add(job)
    db_session.flush()

    # Initial enqueue
    enqueue_job_enrichment(db_session, job.id, "https://example.com/initial")
    enr1 = db_session.query(JobEnrichmentModel).filter_by(job_id=job.id).first()
    assert enr1 is not None
    assert enr1.status == "pending"
    assert enr1.url == "https://example.com/initial"

    # Simulate transition to in_progress
    enr1.status = "in_progress"
    db_session.flush()

    # Second enqueue on the same job_id (conflict path)
    # Must be conflict-safe/idempotent and not raise or overwrite in-progress status
    enqueue_job_enrichment(db_session, job.id, "https://example.com/duplicate")

    enrichments = db_session.query(JobEnrichmentModel).filter_by(job_id=job.id).all()
    assert len(enrichments) == 1
    assert enrichments[0].status == "in_progress"
    assert enrichments[0].url == "https://example.com/initial"

def test_enqueue_preserves_first_discoverer_lineage(db_session):
    """The first-discoverer contract: a deduplicated job must NOT receive a new
    enrichment row, and a later execution must NOT steal the enrichment lineage
    (source_execution_id) from the execution that first discovered the job."""
    from app.db.models.search_execution import SearchExecutionModel
    now = datetime.now(timezone.utc)

    exec1 = SearchExecutionModel(id=770001, candidate_id="LIN::A", status="succeeded", selected_at=now)
    exec2 = SearchExecutionModel(id=770002, candidate_id="LIN::B", status="succeeded", selected_at=now)
    db_session.add_all([exec1, exec2])
    db_session.flush()

    job = JobModel(
        title="Data Engineer",
        company="Data Corp",
        source="jooble",
        source_job_id="lineage_test_770",
        discovered_at=now,
        description="Snippet...",
        description_is_snippet=True,
        url="https://example.com/lineage_job",
    )
    db_session.add(job)
    db_session.flush()

    # First discovery: execution 1 owns the enrichment lineage.
    enqueue_job_enrichment(db_session, job.id, "https://example.com/first", execution_id=exec1.id)
    enr = db_session.query(JobEnrichmentModel).filter_by(job_id=job.id).one()
    assert enr.source_execution_id == exec1.id

    # A later execution re-encounters the SAME job (dedup). on_conflict_do_nothing
    # must leave the existing row untouched: no new row, no lineage transfer.
    enqueue_job_enrichment(db_session, job.id, "https://example.com/second", execution_id=exec2.id)

    rows = db_session.query(JobEnrichmentModel).filter_by(job_id=job.id).all()
    assert len(rows) == 1, "Deduplicated job must not receive a second enrichment row"
    assert rows[0].source_execution_id == exec1.id, "Enrichment lineage must stay with the first discoverer"
    assert rows[0].url == "https://example.com/first", "Existing enrichment row must not be overwritten"

def test_enrichment_worker_populates_lease_holder(db_session):
    job = JobModel(
        title="Platform Engineer",
        company="Cloud Corp",
        source="test",
        source_job_id="lh_test_303",
        discovered_at=datetime.now(timezone.utc),
        description="Snippet...",
        description_is_snippet=True,
        url="https://example.com/job_lh"
    )
    db_session.add(job)
    db_session.flush()

    enrichment = JobEnrichmentModel(
        job_id=job.id,
        status='pending',
        url=job.url
    )
    db_session.add(enrichment)
    db_session.commit()

    with patch.object(EnrichmentWorker, 'process_job'):
        worker = EnrichmentWorker(worker_id="test-worker-alpha")
        worker.claim_and_process(db_session)

        db_session.refresh(enrichment)
        assert enrichment.lease_holder == "test-worker-alpha"
        assert enrichment.status == 'in_progress'
        assert enrichment.lease_token is not None
