import pytest
from app.services.ingestion import acquire_provider_request_slot, DatabaseUnavailable
from sqlalchemy.orm import Session
from sqlalchemy import create_engine

def test_check_usage_db_unavailable():
    # Simulate DB failure by passing a closed session or a mock that raises an exception
    class MockDB:
        def execute(self, *args, **kwargs):
            raise Exception("Connection lost")
        def rollback(self):
            pass
        def commit(self):
            pass

    with pytest.raises(DatabaseUnavailable, match="Quota database unavailable"):
        acquire_provider_request_slot(MockDB(), "adzuna")

from app.schemas.job_search import JobSearchQuery
from app.models.job import Job
from datetime import datetime, timezone
from app.services.ingestion import run_ingestion
from sqlalchemy import text

class DummyProvider2:
    def __init__(self, jobs):
        self.jobs = jobs
    def search_jobs(self, query):
        return self.jobs

def test_run_ingestion_other_integrity_error_via_construct(db_session: Session):
    now = datetime.now(timezone.utc)

    # Bypass Pydantic validation to insert a negative match_score
    # which violates the DB check constraint (match_score >= 0)
    job_bad = Job.model_construct(
        title="J1", company="C1", source="adzuna", source_job_id="1",
        discovered_at=now, location=None, remote=None, employment_type=None,
        description=None, salary_min=None, salary_max=None, currency=None,
        url=None, published_at=None, match_score=-5
    )

    provider = DummyProvider2([job_bad])
    query = JobSearchQuery(keywords="t", location="t")

    result = run_ingestion(db_session, "adzuna", provider, query)

    assert result.fetched == 1
    assert result.created == 0
    assert result.duplicates == 0
    assert result.invalid == 1
