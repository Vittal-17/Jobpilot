import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.main import app
from app.db.database import get_db
from app.db.models.job import JobModel

@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client
    app.dependency_overrides.clear()

@pytest.fixture
def auth_client(client, db_session):
    client.post("/v1/auth/register", json={"email": "jobsuser@example.com", "password": "SecurePassword123!"})
    login_res = client.post("/v1/auth/login", json={"email": "jobsuser@example.com", "password": "SecurePassword123!"})
    token = login_res.headers.get("set-cookie").split(";")[0].split("=")[1].strip()
    client.cookies.set("session_token", token)
    return client

def test_unauthenticated_jobs_access(client):
    client.cookies.clear()
    assert client.get("/v1/jobs").status_code == 401
    assert client.get("/v1/jobs/1").status_code == 401

def test_get_jobs_empty(auth_client):
    res = auth_client.get("/v1/jobs", follow_redirects=False)
    assert res.status_code == 200
    data = res.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["page"] == 1

def test_get_jobs_pagination_and_ordering(auth_client, db_session):
    # Insert 3 jobs with different dates
    now = datetime.now(timezone.utc)
    jobs = [
        JobModel(title="Job A", company="Corp A", source="src", source_job_id="1", discovered_at=now, published_at=datetime(2023, 1, 1, tzinfo=timezone.utc)),
        JobModel(title="Job B", company="Corp B", source="src", source_job_id="2", discovered_at=now, published_at=datetime(2023, 1, 3, tzinfo=timezone.utc)),
        JobModel(title="Job C", company="Corp C", source="src", source_job_id="3", discovered_at=now, published_at=datetime(2023, 1, 2, tzinfo=timezone.utc))
    ]
    db_session.add_all(jobs)
    db_session.commit()

    res = auth_client.get("/v1/jobs?page=1&size=2", follow_redirects=False)
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 3
    assert len(data["items"]) == 2

    # Should be ordered by published_at DESC: Job B, Job C
    assert data["items"][0]["title"] == "Job B"
    assert data["items"][1]["title"] == "Job C"

    # Next page
    res2 = auth_client.get("/v1/jobs?page=2&size=2", follow_redirects=False)
    data2 = res2.json()
    assert len(data2["items"]) == 1
    assert data2["items"][0]["title"] == "Job A"

def test_get_job_detail_success(auth_client, db_session):
    job = JobModel(
        title="Detail Job", company="Detail Corp", source="indeed",
        source_job_id="detail123", discovered_at=datetime.now(timezone.utc),
        salary_min=100000, salary_max=150000, currency="USD",
        match_score=95
    )
    db_session.add(job)
    db_session.commit()

    res = auth_client.get(f"/v1/jobs/{job.id}")
    assert res.status_code == 200
    data = res.json()

    assert data["title"] == "Detail Job"
    assert data["salary_min"] == 100000
    assert "match_score" not in data
    # Ensure internal data doesn't leak
    assert "source_job_id" not in data

def test_get_job_not_found(auth_client):
    res = auth_client.get("/v1/jobs/999999")
    assert res.status_code == 404
    assert res.json()["detail"] == "Job not found"
