import pytest
from fastapi.testclient import TestClient
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

def test_get_me_success(client, db_session):
    client.post(
        "/v1/auth/register",
        json={"email": "meuser@example.com", "password": "SecurePassword123!", "display_name": "Me User"}
    )

    login_res = client.post(
        "/v1/auth/login",
        json={"email": "meuser@example.com", "password": "SecurePassword123!"}
    )
    assert login_res.status_code == 200

    res = client.get("/v1/me")
    assert res.status_code == 200
    data = res.json()
    assert data["email"] == "meuser@example.com"
    assert data["display_name"] == "Me User"
    assert "password" not in data
    assert "password_hash" not in data
    assert "raw_token" not in data

def test_get_me_missing_cookie(client):
    res = client.get("/v1/me")
    assert res.status_code == 401
    assert res.json()["detail"] == "Not authenticated"
    assert "www-authenticate" not in res.headers

def test_get_me_invalid_cookie(client):
    client.cookies.clear()
    client.cookies.set("session_token", "invalidtoken")
    res = client.get("/v1/me")
    assert res.status_code == 401
    assert res.json()["detail"] == "Not authenticated"


def test_get_me_expired_session(client, db_session):
    client.post(
        "/v1/auth/register",
        json={"email": "expired@example.com", "password": "SecurePassword123!"}
    )
    login_res = client.post(
        "/v1/auth/login",
        json={"email": "expired@example.com", "password": "SecurePassword123!"}
    )

    # Extract the cookie token
    raw_token = client.cookies.get("session_token")
    assert raw_token is not None

    # Find the specific session
    from app.db.models.user_session import UserSession
    from app.core.security import hash_session_token
    from sqlalchemy import select, update
    import datetime

    token_hash = hash_session_token(raw_token)
    session = db_session.execute(select(UserSession).where(UserSession.token_hash == token_hash)).scalar_one_or_none()
    assert session is not None

    # Mutate only this session
    db_session.execute(
        update(UserSession)
        .where(UserSession.id == session.id)
        .values(expires_at=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))
    )
    db_session.commit()

    res = client.get("/v1/me")
    assert res.status_code == 401
    assert res.json()["detail"] == "Not authenticated"

def test_get_me_revoked_session(client, db_session):
    client.post(
        "/v1/auth/register",
        json={"email": "revoked@example.com", "password": "SecurePassword123!"}
    )
    login_res = client.post(
        "/v1/auth/login",
        json={"email": "revoked@example.com", "password": "SecurePassword123!"}
    )

    # Extract the cookie token
    raw_token = client.cookies.get("session_token")
    assert raw_token is not None

    # Find the specific session
    from app.db.models.user_session import UserSession
    from app.core.security import hash_session_token
    from sqlalchemy import select, update
    import datetime

    token_hash = hash_session_token(raw_token)
    session = db_session.execute(select(UserSession).where(UserSession.token_hash == token_hash)).scalar_one_or_none()
    assert session is not None

    # Mutate only this session
    db_session.execute(
        update(UserSession)
        .where(UserSession.id == session.id)
        .values(revoked_at=datetime.datetime.now(datetime.timezone.utc))
    )
    db_session.commit()

    res = client.get("/v1/me")
    assert res.status_code == 401

def test_get_me_inactive_user(client, db_session):
    client.post(
        "/v1/auth/register",
        json={"email": "inactive_me@example.com", "password": "SecurePassword123!"}
    )
    login_res = client.post(
        "/v1/auth/login",
        json={"email": "inactive_me@example.com", "password": "SecurePassword123!"}
    )

    # Manually deactivate the user
    from app.db.models.user import User
    from sqlalchemy import update
    db_session.execute(update(User).where(User.email == "inactive_me@example.com").values(is_active=False))
    db_session.commit()

    res = client.get("/v1/me")
    assert res.status_code == 401
