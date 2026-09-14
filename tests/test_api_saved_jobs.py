import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.main import app
from app.db.database import get_db
from app.db.models.job import JobModel
from app.db.models.saved_job import SavedJob

@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client
    app.dependency_overrides.clear()

@pytest.fixture
def auth_context(client):
    def get_token(email):
        client.post("/v1/auth/register", json={"email": email, "password": "SecurePassword123!"})
        res = client.post("/v1/auth/login", json={"email": email, "password": "SecurePassword123!"})
        return res.headers.get("set-cookie").split(";")[0].split("=")[1].strip()
    return get_token

@pytest.fixture
def seed_jobs(db_session):
    now = datetime.now(timezone.utc)
    j1 = JobModel(title="Job 1", company="Corp", source="s", source_job_id="1", discovered_at=now)
    j2 = JobModel(title="Job 2", company="Corp", source="s", source_job_id="2", discovered_at=now)
    db_session.add_all([j1, j2])
    db_session.commit()
    return [j1.id, j2.id]

def test_unauthenticated_access(client):
    client.cookies.clear()
    assert client.post("/v1/saved", json={"job_id": 1}).status_code == 401
    assert client.get("/v1/saved").status_code == 401
    assert client.delete("/v1/saved/1").status_code == 401

def test_save_job_success(client, auth_context, seed_jobs):
    token = auth_context("user1@example.com")
    client.cookies.set("session_token", token)

    res = client.post("/v1/saved", json={"job_id": seed_jobs[0]})
    assert res.status_code == 201
    data = res.json()
    assert "id" in data
    assert "saved_at" in data
    assert data["job"]["title"] == "Job 1"

def test_save_duplicate_job(client, auth_context, seed_jobs):
    token = auth_context("user2@example.com")
    client.cookies.set("session_token", token)

    res1 = client.post("/v1/saved", json={"job_id": seed_jobs[0]})
    assert res1.status_code == 201

    res2 = client.post("/v1/saved", json={"job_id": seed_jobs[0]})
    assert res2.status_code == 409
    assert res2.json()["detail"] == "Job already saved"

def test_save_nonexistent_job(client, auth_context):
    token = auth_context("user3@example.com")
    client.cookies.set("session_token", token)

    res = client.post("/v1/saved", json={"job_id": 99999})
    assert res.status_code == 404
    assert res.json()["detail"] == "Job not found"

def test_list_saved_jobs_ordering_and_isolation(client, auth_context, seed_jobs):
    token_a = auth_context("usera@example.com")
    token_b = auth_context("userb@example.com")

    client.cookies.set("session_token", token_a)
    client.post("/v1/saved", json={"job_id": seed_jobs[0]})
    client.post("/v1/saved", json={"job_id": seed_jobs[1]})

    res_a = client.get("/v1/saved")
    assert res_a.status_code == 200
    data_a = res_a.json()
    assert data_a["total"] == 2
    # Saved Job 2 was saved last, so it should appear first
    assert data_a["items"][0]["job"]["title"] == "Job 2"
    assert data_a["items"][1]["job"]["title"] == "Job 1"

    # User B should see 0
    client.cookies.set("session_token", token_b)
    res_b = client.get("/v1/saved")
    assert res_b.json()["total"] == 0

def test_delete_saved_job(client, auth_context, seed_jobs):
    token = auth_context("user4@example.com")
    client.cookies.set("session_token", token)

    client.post("/v1/saved", json={"job_id": seed_jobs[0]})

    # Delete
    del_res = client.delete(f"/v1/saved/{seed_jobs[0]}")
    assert del_res.status_code == 204

    # Verify missing
    assert client.get("/v1/saved").json()["total"] == 0

    # Idempotent delete
    del_res2 = client.delete(f"/v1/saved/{seed_jobs[0]}")
    assert del_res2.status_code == 204

def test_delete_cross_user_isolation(client, auth_context, seed_jobs):
    token_a = auth_context("isolate_a@example.com")
    token_b = auth_context("isolate_b@example.com")

    client.cookies.set("session_token", token_a)
    client.post("/v1/saved", json={"job_id": seed_jobs[0]})

    client.cookies.set("session_token", token_b)
    # B attempts to delete A's saved job using the same job_id (which B has not saved)
    res_del = client.delete(f"/v1/saved/{seed_jobs[0]}")
    assert res_del.status_code == 204

    # A still has it
    client.cookies.set("session_token", token_a)
    assert client.get("/v1/saved").json()["total"] == 1
