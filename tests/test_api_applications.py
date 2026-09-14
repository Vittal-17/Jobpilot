import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.db.database import get_db
from app.db.models.job import JobModel
from app.db.models.application import Application

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
    j1 = JobModel(title="App Job 1", company="Corp", source="s", source_job_id="a1", discovered_at=now)
    j2 = JobModel(title="App Job 2", company="Corp", source="s", source_job_id="a2", discovered_at=now)
    db_session.add_all([j1, j2])
    db_session.commit()
    return [j1.id, j2.id]

def test_unauthenticated_access(client):
    client.cookies.clear()
    assert client.post("/v1/applications", json={"job_id": 1}).status_code == 401
    assert client.get("/v1/applications").status_code == 401
    assert client.get("/v1/applications/1").status_code == 401
    assert client.patch("/v1/applications/1", json={"status": "rejected"}).status_code == 401

def test_create_application_success(client, auth_context, seed_jobs):
    token = auth_context("appuser1@example.com")
    client.cookies.set("session_token", token)

    res = client.post("/v1/applications", json={"job_id": seed_jobs[0]})
    assert res.status_code == 201
    data = res.json()
    assert data["status"] == "applied"
    assert "created_at" in data
    assert "updated_at" in data
    assert data["job"]["title"] == "App Job 1"

def test_create_application_nonexistent_job(client, auth_context):
    token = auth_context("appuser2@example.com")
    client.cookies.set("session_token", token)

    res = client.post("/v1/applications", json={"job_id": 999999})
    assert res.status_code == 404
    assert res.json()["detail"] == "Job not found"

def test_create_application_duplicate(client, auth_context, seed_jobs):
    token = auth_context("appuser3@example.com")
    client.cookies.set("session_token", token)

    client.post("/v1/applications", json={"job_id": seed_jobs[1]})
    res = client.post("/v1/applications", json={"job_id": seed_jobs[1]})

    assert res.status_code == 409
    assert res.json()["detail"] == "Application already exists for this job"

def test_list_applications_ordering(client, auth_context, seed_jobs):
    token = auth_context("appuser4@example.com")
    client.cookies.set("session_token", token)

    client.post("/v1/applications", json={"job_id": seed_jobs[0]})
    client.post("/v1/applications", json={"job_id": seed_jobs[1]})

    res = client.get("/v1/applications")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    # Ordered by updated_at desc -> Job 2 was created last
    assert data["items"][0]["job"]["title"] == "App Job 2"
    assert data["items"][1]["job"]["title"] == "App Job 1"

def test_get_application_detail_isolation(client, auth_context, seed_jobs):
    token_a = auth_context("appuser5a@example.com")
    token_b = auth_context("appuser5b@example.com")

    client.cookies.set("session_token", token_a)
    res_create = client.post("/v1/applications", json={"job_id": seed_jobs[0]})
    app_id = res_create.json()["id"]

    res_get_a = client.get(f"/v1/applications/{app_id}")
    assert res_get_a.status_code == 200

    client.cookies.set("session_token", token_b)
    res_get_b = client.get(f"/v1/applications/{app_id}")
    assert res_get_b.status_code == 404

def test_update_application_status(client, auth_context, seed_jobs):
    token = auth_context("appuser6@example.com")
    client.cookies.set("session_token", token)

    res_create = client.post("/v1/applications", json={"job_id": seed_jobs[0]})
    app_id = res_create.json()["id"]

    res_patch = client.patch(f"/v1/applications/{app_id}", json={"status": "interviewing"})
    assert res_patch.status_code == 200
    assert res_patch.json()["status"] == "interviewing"

def test_update_application_invalid_status(client, auth_context, seed_jobs):
    token = auth_context("appuser7@example.com")
    client.cookies.set("session_token", token)

    res_create = client.post("/v1/applications", json={"job_id": seed_jobs[0]})
    app_id = res_create.json()["id"]

    # Pydantic Literal handles validation natively
    res_patch = client.patch(f"/v1/applications/{app_id}", json={"status": "ghosted"})
    assert res_patch.status_code == 422

def test_update_application_immutability(client, auth_context, seed_jobs, db_session):
    token = auth_context("appuser8@example.com")
    client.cookies.set("session_token", token)

    # Create
    res_create = client.post("/v1/applications", json={"job_id": seed_jobs[0]})
    app_id = res_create.json()["id"]

    # Get user_id for verification
    from sqlalchemy import select
    from app.db.models.user import User
    from app.db.models.application import Application
    user_id = db_session.execute(select(User.id).where(User.email == "appuser8@example.com")).scalar_one()

    # Patch with prohibited fields
    res_patch = client.patch(f"/v1/applications/{app_id}", json={
        "status": "interviewing",
        "job_id": seed_jobs[1],
        "user_id": 99999,
        "created_at": "1999-01-01T00:00:00Z"
    })

    # Even if pydantic ignores them (returning 200), we must prove the DB didn't change them
    assert res_patch.status_code == 200
    assert res_patch.json()["status"] == "interviewing"
    assert res_patch.json()["job"]["title"] == "App Job 1" # Did not switch to App Job 2

    # Verify in DB directly
    db_session.expire_all()
    db_app = db_session.execute(select(Application).where(Application.id == app_id)).scalar_one()
    assert db_app.job_id == seed_jobs[0]
    assert db_app.user_id == user_id
    assert db_app.created_at.year != 1999

def test_create_application_unexpected_integrity_error(client, auth_context, seed_jobs, monkeypatch):
    token = auth_context("appuser9@example.com")
    client.cookies.set("session_token", token)

    def mock_commit(*args, **kwargs):
        from sqlalchemy.exc import IntegrityError
        raise IntegrityError("mock error", "mock params", "mock orig")

    # Mock the db session commit directly in the endpoint dependency
    from app.api.endpoints import applications
    import sqlalchemy.orm.session
    original_commit = sqlalchemy.orm.session.Session.commit

    monkeypatch.setattr("sqlalchemy.orm.session.Session.commit", mock_commit)

    res = client.post("/v1/applications", json={"job_id": seed_jobs[0]})

    # Should be 500, not 409, because the record doesn't actually exist
    assert res.status_code == 500
    assert res.json()["detail"] == "Internal server error"

    # restore to avoid breaking subsequent tests if monkeypatch leaks (it shouldn't but good practice)
    monkeypatch.setattr("sqlalchemy.orm.session.Session.commit", original_commit)
