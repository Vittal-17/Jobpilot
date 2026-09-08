import os
import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError
from pydantic import HttpUrl

from app.core.config import settings
from app.db.base import Base
from app.db.models.job import JobModel
from app.models.job import Job as PydanticJob
from app.db.repository.job_repository import save_job, get_job_by_source_id

# Use a test database URL or the default one


def test_database_connectivity(engine):
    with engine.connect() as conn:
        assert not conn.closed

def test_insert_and_read_job(db_session):
    now = datetime.now(timezone.utc)
    pydantic_job = PydanticJob(
        title="Software Engineer",
        company="TechCorp",
        source="LinkedIn",
        source_job_id="12345",
        discovered_at=now,
        location="San Francisco, CA",
        remote=True,
        employment_type="Full-time",
        description="A great job.",
        salary_min=100000,
        salary_max=150000,
        currency="USD",
        url="https://linkedin.com/jobs/12345",
        published_at=now,
        match_score=95,
    )

    db_job = save_job(db_session, pydantic_job)
    assert db_job.id is not None
    assert db_job.title == "Software Engineer"

    retrieved_job = get_job_by_source_id(db_session, "LinkedIn", "12345")
    assert retrieved_job is not None
    assert retrieved_job.title == "Software Engineer"
    assert retrieved_job.salary_max == 150000

def test_nullable_fields_allowed(db_session):
    now = datetime.now(timezone.utc)
    pydantic_job = PydanticJob(
        title="Backend Developer",
        company="StartupInc",
        source="HackerNews",
        source_job_id="hn_987",
        discovered_at=now,
    )

    db_job = save_job(db_session, pydantic_job)
    assert db_job.salary_min is None
    assert db_job.remote is None
    assert db_job.location is None

def test_uniqueness_constraint(db_session):
    now = datetime.now(timezone.utc)
    job1 = PydanticJob(
        title="Job 1",
        company="Company A",
        source="Source X",
        source_job_id="ABC",
        discovered_at=now,
    )
    save_job(db_session, job1)

    job2 = PydanticJob(
        title="Job 2",
        company="Company A",
        source="Source X",
        source_job_id="ABC",
        discovered_at=now,
    )
    with pytest.raises(IntegrityError):
        save_job(db_session, job2)

def test_invalid_score_db_constraint(db_session):
    now = datetime.now(timezone.utc)
    # We bypass pydantic validation here to test the DB constraint
    db_job = JobModel(
        title="Job", company="Co", source="S", source_job_id="1",
        discovered_at=now, match_score=150
    )
    db_session.add(db_job)
    with pytest.raises(IntegrityError):
        db_session.commit()

def test_invalid_salary_db_constraint(db_session):
    now = datetime.now(timezone.utc)
    db_job = JobModel(
        title="Job", company="Co", source="S2", source_job_id="2",
        discovered_at=now, salary_min=-50000
    )
    db_session.add(db_job)
    with pytest.raises(IntegrityError):
        db_session.commit()
