import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.db.database import get_db

@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client
    app.dependency_overrides.clear()

def test_get_profile_missing(client, db_session):
    client.post("/v1/auth/register", json={"email": "noprofile@example.com", "password": "SecurePassword123!"})
    client.post("/v1/auth/login", json={"email": "noprofile@example.com", "password": "SecurePassword123!"})

    res = client.get("/v1/profile")
    assert res.status_code == 404
    assert res.json()["detail"] == "Profile not found"

def test_upsert_profile_success(client, db_session):
    client.post("/v1/auth/register", json={"email": "upsert@example.com", "password": "SecurePassword123!"})
    client.post("/v1/auth/login", json={"email": "upsert@example.com", "password": "SecurePassword123!"})

    # Upsert Profile
    res = client.patch(
        "/v1/profile",
        json={
            "headline": "Backend Engineer",
            "experience_years": 5,
            "remote_preference": "hybrid"
        }
    )
    assert res.status_code == 200
    data = res.json()
    assert data["headline"] == "Backend Engineer"
    assert data["experience_years"] == 5
    assert data["remote_preference"] == "hybrid"
    assert data["location"] is None

    # Retrieve it
    get_res = client.get("/v1/profile")
    assert get_res.status_code == 200
    assert get_res.json() == data

def test_patch_profile_partial_update(client, db_session):
    client.post("/v1/auth/register", json={"email": "partial@example.com", "password": "SecurePassword123!"})
    client.post("/v1/auth/login", json={"email": "partial@example.com", "password": "SecurePassword123!"})

    # Create
    client.patch("/v1/profile", json={"headline": "Software Engineer", "location": "NYC"})

    # Partial update
    res = client.patch("/v1/profile", json={"location": "SF", "experience_years": 3})
    assert res.status_code == 200
    data = res.json()
    # Updated fields
    assert data["location"] == "SF"
    assert data["experience_years"] == 3
    # Unset fields remain unchanged
    assert data["headline"] == "Software Engineer"
    # Nullable fields can be explicitly cleared
    res2 = client.patch("/v1/profile", json={"headline": None})
    assert res2.status_code == 200
    assert res2.json()["headline"] is None

def test_patch_profile_negative_experience(client, db_session):
    client.post("/v1/auth/register", json={"email": "negative@example.com", "password": "SecurePassword123!"})
    client.post("/v1/auth/login", json={"email": "negative@example.com", "password": "SecurePassword123!"})

    res = client.patch("/v1/profile", json={"experience_years": -1})
    assert res.status_code == 422

    # Verify the specific validation error produced by Pydantic ge=0
    errors = res.json().get("detail", [])
    assert len(errors) > 0
    assert errors[0]["loc"] == ["body", "experience_years"]

def test_unauthenticated_profile_access(client):
    client.cookies.clear()
    res = client.get("/v1/profile")
    assert res.status_code == 401

    res2 = client.patch("/v1/profile", json={"headline": "Hacker"})
    assert res2.status_code == 401

def test_cross_user_isolation(client, db_session):
    # User A creates a profile
    client.post("/v1/auth/register", json={"email": "usera@example.com", "password": "SecurePassword123!"})
    login_a = client.post("/v1/auth/login", json={"email": "usera@example.com", "password": "SecurePassword123!"})
    token_a = login_a.headers.get("set-cookie").split(";")[0].split("=")[1].strip()

    client.cookies.set("session_token", token_a)
    client.patch("/v1/profile", json={"headline": "User A Headline"})

    client.cookies.clear()

    # User B registers and logs in
    client.post("/v1/auth/register", json={"email": "userb@example.com", "password": "SecurePassword123!"})
    login_b = client.post("/v1/auth/login", json={"email": "userb@example.com", "password": "SecurePassword123!"})
    token_b = login_b.headers.get("set-cookie").split(";")[0].split("=")[1].strip()

    client.cookies.set("session_token", token_b)

    # User B gets their own profile -> missing
    res_b = client.get("/v1/profile")
    assert res_b.status_code == 404

    # Attempt to fetch User A's profile is impossible via API since it's derived from token
    # Create User B profile
    client.patch("/v1/profile", json={"headline": "User B Headline"})

    res_b2 = client.get("/v1/profile")
    assert res_b2.json()["headline"] == "User B Headline"

    # Verify User A's profile wasn't touched
    client.cookies.clear()
    client.cookies.set("session_token", token_a)
    res_a = client.get("/v1/profile")
    assert res_a.json()["headline"] == "User A Headline"
