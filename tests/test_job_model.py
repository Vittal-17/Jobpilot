import pytest
from datetime import datetime, timezone
from pydantic import ValidationError

from app.models.job import Job


def test_valid_job_all_fields():
    now = datetime.now(timezone.utc)
    job = Job(
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
    assert job.title == "Software Engineer"
    assert job.salary_min == 100000
    assert job.match_score == 95
    assert str(job.url) == "https://linkedin.com/jobs/12345"


def test_minimal_valid_job():
    now = datetime.now(timezone.utc)
    job = Job(
        title="Backend Developer",
        company="StartupInc",
        source="HackerNews",
        source_job_id="hn_987",
        discovered_at=now,
    )
    assert job.title == "Backend Developer"
    assert job.salary_min is None
    assert job.remote is None
    assert job.location is None


def test_invalid_match_score():
    now = datetime.now(timezone.utc)
    base_data = {
        "title": "Engineer",
        "company": "Corp",
        "source": "Board",
        "source_job_id": "1",
        "discovered_at": now,
    }

    with pytest.raises(ValidationError):
        Job(**base_data, match_score=-1)

    with pytest.raises(ValidationError):
        Job(**base_data, match_score=101)


def test_invalid_salary():
    now = datetime.now(timezone.utc)
    base_data = {
        "title": "Engineer",
        "company": "Corp",
        "source": "Board",
        "source_job_id": "1",
        "discovered_at": now,
    }

    with pytest.raises(ValidationError):
        Job(**base_data, salary_min=-50000)

    with pytest.raises(ValidationError):
        Job(**base_data, salary_max=-1)


def test_valid_url():
    now = datetime.now(timezone.utc)
    job = Job(
        title="Engineer",
        company="Corp",
        source="Board",
        source_job_id="1",
        discovered_at=now,
        url="https://www.example.com/job/1"
    )
    assert str(job.url) == "https://www.example.com/job/1"


def test_invalid_url():
    now = datetime.now(timezone.utc)
    base_data = {
        "title": "Engineer",
        "company": "Corp",
        "source": "Board",
        "source_job_id": "1",
        "discovered_at": now,
    }

    with pytest.raises(ValidationError):
        Job(**base_data, url="not_a_valid_url")

def test_job_validation_empty_whitespace():
    with pytest.raises(ValueError, match="Field cannot be empty"):
        Job(
            title="   ",
            company="Test Co",
            source="test",
            source_job_id="123",
            discovered_at=datetime.now(timezone.utc)
        )

def test_salary_min_max_consistency():
    with pytest.raises(ValueError, match="salary_min cannot be greater than salary_max"):
        Job(
            title="Dev",
            company="Test Co",
            source="test",
            source_job_id="123",
            discovered_at=datetime.now(timezone.utc),
            salary_min=100000,
            salary_max=80000
        )
