import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.db.database import get_db
from app.db.models.user import User

@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()

def test_register_success(client, db_session):
    response = client.post(
        "/v1/auth/register",
        json={"email": "NewUser@Example.com", "password": "SecurePassword123!", "display_name": "New User"}
    )
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "newuser@example.com"
    assert data["display_name"] == "New User"
    assert data["is_active"] is True
    assert "id" in data
    assert "created_at" in data

    # Assert safe response contents
    assert "password" not in data
    assert "password_hash" not in data

    # Assert DB persistence
    user = db_session.execute(select(User).where(User.email == "newuser@example.com")).scalar_one_or_none()
    assert user is not None
    assert user.display_name == "New User"

def test_register_duplicate_email(client):
    # Register first time
    response = client.post(
        "/v1/auth/register",
        json={"email": "duplicate@example.com", "password": "SecurePassword123!"}
    )
    assert response.status_code == 201

    # Register again with case variation
    response = client.post(
        "/v1/auth/register",
        json={"email": "DUPLICATE@example.com", "password": "AnotherPassword456!"}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Email already registered"

def test_register_invalid_input(client):
    # Missing required field
    response = client.post(
        "/v1/auth/register",
        json={"email": "invalid@example.com"}
    )
    assert response.status_code == 422

    # Invalid email format based on pydantic schema pattern (must contain @)
    response = client.post(
        "/v1/auth/register",
        json={"email": "invalidemail", "password": "SecurePassword123!"}
    )
    assert response.status_code == 422

    # Too short password
    response = client.post(
        "/v1/auth/register",
        json={"email": "short@example.com", "password": "short"}
    )
    assert response.status_code == 422

def test_register_oversized_password(client):
    response = client.post(
        "/v1/auth/register",
        json={"email": "oversize@example.com", "password": "a" * 73}
    )
    assert response.status_code == 400
    assert "maximum supported length" in response.json()["detail"].lower()
