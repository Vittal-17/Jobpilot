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

    db_job, created = save_job(db_session, pydantic_job)
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

    db_job, created = save_job(db_session, pydantic_job)
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
    db_job2, created2 = save_job(db_session, job2)
    assert created2 is False

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

def test_description_is_snippet_persistence(db_session):
    now = datetime.now(timezone.utc)
    # 1. PydanticJob defaults to True
    pydantic_job_default = PydanticJob(
        title="Snippet Job",
        company="Co",
        source="S3",
        source_job_id="3",
        discovered_at=now,
    )
    assert pydantic_job_default.description_is_snippet is True
    db_job1, _ = save_job(db_session, pydantic_job_default)
    assert db_job1.description_is_snippet is True

    # 2. PydanticJob explicit False
    pydantic_job_false = PydanticJob(
        title="Full Text Job",
        company="Co",
        source="S4",
        source_job_id="4",
        discovered_at=now,
        description_is_snippet=False
    )
    assert pydantic_job_false.description_is_snippet is False
    db_job2, _ = save_job(db_session, pydantic_job_false)
    assert db_job2.description_is_snippet is False

    # 3. Direct DB insert without explicit value relies on DB default/Python default
    db_job3 = JobModel(
        title="DB Default Job", company="Co", source="S5", source_job_id="5",
        discovered_at=now
    )
    db_session.add(db_job3)
    db_session.commit()
    db_session.refresh(db_job3)
    # The Mapped column default=True should have applied
    assert db_job3.description_is_snippet is True

    # 4. Raw DB-level insert omitting description_is_snippet entirely
    # Proves the PostgreSQL server default itself is true without SQLAlchemy Python-side defaults
    from sqlalchemy import text
    db_session.execute(
        text(
            """
            INSERT INTO jobs (title, company, source, source_job_id, discovered_at, created_at, updated_at)
            VALUES ('Raw DB Default Job', 'Raw Co', 'S_RAW', 'raw_1', :now, :now, :now)
            """
        ),
        {"now": now}
    )
    db_session.commit()
    raw_val = db_session.execute(
        text("SELECT description_is_snippet FROM jobs WHERE source = 'S_RAW' AND source_job_id = 'raw_1'")
    ).scalar_one()
    assert raw_val is True

    # 5. Raw DB-level insert explicitly specifying false persists as false
    db_session.execute(
        text(
            """
            INSERT INTO jobs (title, company, source, source_job_id, discovered_at, created_at, updated_at, description_is_snippet)
            VALUES ('Raw DB Full Job', 'Raw Co', 'S_RAW_FULL', 'raw_2', :now, :now, :now, false)
            """
        ),
        {"now": now}
    )
    db_session.commit()
    raw_false = db_session.execute(
        text("SELECT description_is_snippet FROM jobs WHERE source = 'S_RAW_FULL' AND source_job_id = 'raw_2'")
    ).scalar_one()
    assert raw_false is False

    # 6. Verify PostgreSQL column metadata: column_default is 'true' and is_nullable is 'NO'
    col_info = db_session.execute(
        text(
            """
            SELECT column_default, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'jobs' AND column_name = 'description_is_snippet'
            """
        )
    ).one()
    assert col_info[0] == 'true'
    assert col_info[1] == 'NO'


def test_migration_semantics_for_existing_jobs(db_session):
    """
    Verify migration behavior for pre-existing jobs:
    If a row exists without explicit snippet provenance, PostgreSQL's server default
    guarantees that existing rows received true, safely classifying them as incomplete evidence.
    """
    from sqlalchemy import text
    now = datetime.now(timezone.utc)

    # Insert jobs with adzuna, jooble, and unknown sources omitting description_is_snippet
    db_session.execute(
        text(
            """
            INSERT INTO jobs (title, company, source, source_job_id, discovered_at, created_at, updated_at)
            VALUES
                ('Legacy Adzuna Job', 'LegacyCo', 'adzuna', 'adz_legacy_1', :now, :now, :now),
                ('Legacy Jooble Job', 'LegacyCo', 'jooble', 'jbl_legacy_1', :now, :now, :now),
                ('Legacy Unknown Job', 'LegacyCo', 'unknown_source', 'unk_legacy_1', :now, :now, :now)
            """
        ),
        {"now": now}
    )
    db_session.commit()

    rows = db_session.execute(
        text(
            """
            SELECT source, description_is_snippet
            FROM jobs
            WHERE source IN ('adzuna', 'jooble', 'unknown_source')
              AND source_job_id IN ('adz_legacy_1', 'jbl_legacy_1', 'unk_legacy_1')
            ORDER BY source
            """
        )
    ).all()

    assert len(rows) == 3
    for source, is_snippet in rows:
        assert is_snippet is True, f"Expected {source} to be True, got {is_snippet}"
