import pytest
from datetime import datetime, timezone
from app.services.ingestion import run_ingestion
from app.schemas.job_search import JobSearchQuery
from app.models.job import Job
from app.db.models.job import JobModel
from sqlalchemy.orm import Session

class DummyProvider:
    def __init__(self, jobs):
        self.jobs = jobs
    def search_jobs(self, query):
        return self.jobs

def test_ingestion_service_success_and_duplicates(db_session: Session):
    now = datetime.now(timezone.utc)
    job1 = Job(
        title="J1", company="C1", source="adzuna", source_job_id="1", discovered_at=now
    )
    job2 = Job(
        title="J2", company="C2", source="adzuna", source_job_id="2", discovered_at=now
    )

    provider = DummyProvider([job1, job2])
    query = JobSearchQuery(keywords="t", location="t")

    result = run_ingestion(db_session, "adzuna", provider, query)
    assert result.fetched == 2
    assert result.created == 2
    assert result.duplicates == 0

    # Run again, should be duplicates
    result2 = run_ingestion(db_session, "adzuna", provider, query)
    assert result2.fetched == 2
    assert result2.created == 0
    assert result2.duplicates == 2
