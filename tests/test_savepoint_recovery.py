import pytest
from sqlalchemy.exc import IntegrityError
from app.db.models.job import JobModel
from datetime import datetime, timezone
from app.services.ingestion import save_job

def test_savepoint_recovery(db_session):
    # Job A valid
    job_a = JobModel(title="A", company="C", source="adzuna", source_job_id="A", discovered_at=datetime.now(timezone.utc))
    with db_session.begin_nested():
        save_job(db_session, job_a)

    # Job B fails (empty title triggers db constraint)
    job_b = JobModel(title="", company="C", source="adzuna", source_job_id="B", discovered_at=datetime.now(timezone.utc))
    try:
        with db_session.begin_nested():
            save_job(db_session, job_b)
    except IntegrityError:
        pass # Savepoint rolls back naturally

    # Job C valid
    job_c = JobModel(title="C", company="C", source="adzuna", source_job_id="C", discovered_at=datetime.now(timezone.utc))
    with db_session.begin_nested():
        save_job(db_session, job_c)

    db_session.commit()

    # Assert A and C are in DB, B is not
    jobs = db_session.query(JobModel).all()
    assert len(jobs) == 2
    ids = [j.source_job_id for j in jobs]
    assert "A" in ids
    assert "C" in ids
    assert "B" not in ids
