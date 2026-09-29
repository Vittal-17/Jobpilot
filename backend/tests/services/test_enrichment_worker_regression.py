import pytest
from datetime import datetime, timezone, timedelta
import httpx
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text

from app.services.enrichment_worker import EnrichmentWorker
from app.db.models.job import JobModel
from app.db.models.job_enrichment import JobEnrichmentModel
from app.db.models.user_profile import UserProfile
from app.db.models.user_search import UserSearch
from app.db.models.recommendation_history import RecommendationHistoryModel
from app.db.models.user import User

def test_request_exception_falls_back_to_snippet(engine, monkeypatch):
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    user_id = 99991
    job_id = 99991

    try:
        # Clean up first
        db.query(RecommendationHistoryModel).filter_by(job_id=job_id).delete()
        db.query(JobEnrichmentModel).filter_by(job_id=job_id).delete()
        db.query(JobModel).filter_by(id=job_id).delete()
        db.query(UserSearch).filter_by(user_id=user_id).delete()
        db.query(UserProfile).filter_by(user_id=user_id).delete()
        db.query(User).filter_by(id=user_id).delete()
        db.commit()
    except Exception:
        db.rollback()

    try:
        # Create User, Profile and active search
        db.add(User(id=user_id, email="test_fallback@example.com", password_hash="test"))
        db.add(UserProfile(user_id=user_id, preferred_roles="Software Engineer", preferred_locations="Remote", experience_years=2))
        db.add(UserSearch(user_id=user_id, query="Software Engineer", location="Remote", enabled=True))

        # Create a Job with snippet
        job = JobModel(
            id=job_id,
            source="test-provider",
            source_job_id="test-job-99991",
            title="Junior Software Engineer",
            company="Acme Corp",
            location="Remote",
            description="We are looking for a Junior Software Engineer with Python skills. 0 years experience required.",
            url="https://example.com/job",
            discovered_at=datetime.now(timezone.utc),
            description_is_snippet=True,
        )
        db.add(job)

        # Create JobEnrichment
        token = "test-lease-token"
        enrich = JobEnrichmentModel(
            job_id=job_id,
            status="pending",
            url="https://example.com/job",
            lease_token=token,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)
        )
        db.add(enrich)
        db.commit()

        # 2. Mock ssrf_client to raise HTTPError
        class MockSSRFClient:
            def fetch(self, url):
                # Simulate a 403 HTTPStatusError
                request = httpx.Request("GET", url)
                response = httpx.Response(403, request=request)
                raise httpx.HTTPStatusError("403 Forbidden", request=request, response=response)

        worker = EnrichmentWorker()
        worker.ssrf_client = MockSSRFClient()

        # 3. Process the job
        worker.process_job(db, job_id, "https://example.com/job", token)

        # 4. Verify JobEnrichment state (should be unsupported, not failure/retry)
        enrich_after = db.query(JobEnrichmentModel).filter_by(job_id=job_id).first()
        assert enrich_after.status == "unsupported"

        # 5. Verify Recommendation exists
        rec = db.query(RecommendationHistoryModel).filter_by(job_id=job_id, user_id=user_id).first()
        assert rec is not None
        assert rec.score >= 50

        # 6. Prove idempotency (repeated evaluation does not duplicate recommendation)
        # Reset enrichment state so the job is "re-evaluated" completely
        db.execute(text("UPDATE job_enrichments SET status = 'pending' WHERE job_id = :job_id"), {"job_id": job_id})
        db.commit()

        worker.process_job(db, job_id, "https://example.com/job", token)

        # Verify count remains exactly 1 despite a full re-evaluation
        rec_count = db.query(RecommendationHistoryModel).filter_by(job_id=job_id, user_id=user_id).count()
        assert rec_count == 1

    finally:
        try:
            db.query(RecommendationHistoryModel).filter_by(job_id=job_id).delete()
            db.query(JobEnrichmentModel).filter_by(job_id=job_id).delete()
            db.query(JobModel).filter_by(id=job_id).delete()
            db.query(UserSearch).filter_by(user_id=user_id).delete()
            db.query(UserProfile).filter_by(user_id=user_id).delete()
            db.query(User).filter_by(id=user_id).delete()
            db.commit()
        except Exception:
            db.rollback()
        db.close()


def test_unexpected_exception_uses_complete_failure(engine, monkeypatch):
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    job_id = 99992

    try:
        # Clean up
        db.query(JobEnrichmentModel).filter_by(job_id=job_id).delete()
        db.query(JobModel).filter_by(id=job_id).delete()
        db.commit()
    except Exception:
        db.rollback()

    try:
        # Create Job
        job = JobModel(
            id=job_id,
            source="test-provider",
            source_job_id="test-job-99992",
            title="Junior Software Engineer",
            company="Acme Corp",
            location="Remote",
            description="Snippet",
            url="https://example.com/job2",
            discovered_at=datetime.now(timezone.utc),
            description_is_snippet=True,
        )
        db.add(job)

        # Create JobEnrichment
        token = "test-lease-token-2"
        enrich = JobEnrichmentModel(
            job_id=job_id,
            status="pending",
            url="https://example.com/job2",
            lease_token=token,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)
        )
        db.add(enrich)
        db.commit()

        # Mock ssrf_client to raise a generic ValueError
        class MockSSRFClient:
            def fetch(self, url):
                raise ValueError("Something completely unexpected happened")

        worker = EnrichmentWorker()
        worker.ssrf_client = MockSSRFClient()

        # Process the job
        worker.process_job(db, job_id, "https://example.com/job2", token)

        # Verify JobEnrichment state (should be retry as part of complete_failure)
        enrich_after = db.query(JobEnrichmentModel).filter_by(job_id=job_id).first()
        assert enrich_after.status == "retry"
        assert enrich_after.attempts == 1

        # Verify Recommendation does NOT exist
        rec_count = db.query(RecommendationHistoryModel).filter_by(job_id=job_id).count()
        assert rec_count == 0

    finally:
        try:
            db.query(JobEnrichmentModel).filter_by(job_id=job_id).delete()
            db.query(JobModel).filter_by(id=job_id).delete()
            db.commit()
        except Exception:
            db.rollback()
        db.close()
