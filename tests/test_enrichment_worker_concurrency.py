import pytest
import time
import threading
from datetime import datetime, timezone, timedelta
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from app.db.models.job import JobModel
from app.db.models.job_enrichment import JobEnrichmentModel
from app.services.enrichment_worker import EnrichmentWorker

def test_lease_expiry_under_row_lock(engine):
    # 'engine' fixture is from conftest.py (or we can use db_session's bind)
    Session = sessionmaker(bind=engine)

    # We use independent sessions.
    with Session() as setup_db:
        # Setup job and enrichment
        job = JobModel(
            title="Lock Test",
            company="Tech Corp",
            source="test",
            source_job_id="lock_1",
            discovered_at=datetime.now(timezone.utc),
            description="Snippet...",
            description_is_snippet=True,
            url="https://example.com/job"
        )
        setup_db.add(job)
        setup_db.commit()
        job_id = job.id

        token = "lock_token"
        # Use DB clock for synchronization
        db_now = setup_db.execute(text("SELECT clock_timestamp()")).scalar()
        expires = db_now + timedelta(seconds=1.5)

        enrichment = JobEnrichmentModel(
            job_id=job.id,
            status='in_progress',
            url=job.url,
            lease_token=token,
            lease_expires_at=expires
        )
        setup_db.add(enrichment)
        setup_db.commit()

    thread_errors = []

    def worker_b_task():
        try:
            with Session() as db_b:
                worker = EnrichmentWorker()
                # This will block because session A holds the lock.
                worker.complete_success(db_b, job_id, token, "New Full Description")
        except Exception as e:
            thread_errors.append(e)

    try:
        # Session A holds lock AND modifies the row to trigger EPQ re-evaluation
        # If it only SELECTs and rolls back, Postgres will skip re-evaluating clock_timestamp()
        with Session() as db_a:
            db_a.execute(text("UPDATE job_enrichments SET status = status WHERE job_id = :jid"), {"jid": job_id})

            # Start Session B which will block
            t = threading.Thread(target=worker_b_task)
            t.start()

            # Wait for the lease to expire while Session B is blocked
            time.sleep(2.0)

            # Commit releases the lock and signals modification, forcing B to re-evaluate its WHERE clause
            db_a.commit()

        t.join(timeout=5)

        # Check thread did not crash
        assert not thread_errors, f"Thread raised exceptions: {thread_errors}"
        assert not t.is_alive(), "Worker B thread hung"

        # Check that Session B did NOT update the job or enrichment
        with Session() as check_db:
            job_check = check_db.get(JobModel, job_id)
            enr_check = check_db.query(JobEnrichmentModel).filter_by(job_id=job_id).first()

            assert enr_check.status == 'in_progress'
            assert job_check.description_is_snippet is True
            assert job_check.description == "Snippet..."

    finally:
        # Cleanup
        with Session() as clean_db:
            clean_db.execute(text("DELETE FROM job_enrichments WHERE job_id = :jid"), {"jid": job_id})
            clean_db.execute(text("DELETE FROM jobs WHERE id = :jid"), {"jid": job_id})
            clean_db.commit()
