import pytest
from sqlalchemy.exc import IntegrityError
from app.db.models.job import JobModel
from datetime import datetime, timezone

def create_valid_job(session):
    return JobModel(
        title="Valid Title",
        company="Valid Company",
        source="adzuna",
        source_job_id="12345",
        discovered_at=datetime.now(timezone.utc)
    )

def test_string_constraints(db_session):
    # Test empty string title
    job = create_valid_job(db_session)
    job.title = ""
    db_session.add(job)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Test whitespace title
    job = create_valid_job(db_session)
    job.title = "   "
    db_session.add(job)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Test empty company
    job = create_valid_job(db_session)
    job.company = " "
    db_session.add(job)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

def test_salary_constraints(db_session):
    # Min > Max should fail
    job = create_valid_job(db_session)
    job.salary_min = 100000
    job.salary_max = 50000
    db_session.add(job)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Min < Max should pass
    job = create_valid_job(db_session)
    job.salary_min = 50000
    job.salary_max = 100000
    db_session.add(job)
    db_session.commit()

def test_source_and_id_constraints(db_session):
    # Test empty source
    job = create_valid_job(db_session)
    job.source = " "
    db_session.add(job)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Test empty source_job_id
    job = create_valid_job(db_session)
    job.source_job_id = ""
    db_session.add(job)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

def test_named_constraints_exist(engine):
    from sqlalchemy import text
    with engine.connect() as conn:
        result = conn.execute(text("SELECT conname FROM pg_constraint WHERE conrelid = 'jobs'::regclass")).scalars().all()
        assert "chk_jobs_salary_range" in result
        assert "chk_jobs_title_not_empty" in result
        assert "chk_jobs_company_not_empty" in result
        assert "chk_jobs_source_not_empty" in result
        assert "chk_jobs_source_job_id_not_empty" in result
