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

def test_login_success(client, db_session):
    # Register a user first
    client.post(
        "/v1/auth/register",
        json={"email": "loginuser@example.com", "password": "SecurePassword123!", "display_name": "Login User"}
    )

    # Login
    response = client.post(
        "/v1/auth/login",
        json={"email": "loginuser@example.com", "password": "SecurePassword123!"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "loginuser@example.com"
    assert "password" not in data
    assert "password_hash" not in data
    assert "token" not in data
    assert "raw_token" not in data

    # Check cookie
    cookies = response.cookies
    assert "session_token" in cookies

    # In httpx/TestClient, cookie attributes can be checked via the cookie jar
    cookie = next((c for c in client.cookies.jar if c.name == "session_token"), None)
    assert cookie is not None
    # We can't easily assert HttpOnly/Secure via httpx cookie jar directly in all versions,
    # but we can check the raw headers.
    set_cookie_header = response.headers.get("set-cookie")
    assert "session_token=" in set_cookie_header
    assert "HttpOnly" in set_cookie_header
    assert "Secure" in set_cookie_header
    assert "samesite=lax" in set_cookie_header.lower()
    assert "expires=" in set_cookie_header.lower()
    assert "gmt" in set_cookie_header.lower()

    import email.utils
    parts = set_cookie_header.split(";")
    expires_str = next((p.split("=")[1].strip() for p in parts if p.strip().lower().startswith("expires=")), None)
    assert expires_str is not None
    cookie_expires_dt = email.utils.parsedate_to_datetime(expires_str)

    # Ensure session was created in DB
    from app.db.models.user_session import UserSession
    from sqlalchemy import select
    # The cookie value is the raw token
    raw_token = cookie.value
    from app.core.security import hash_session_token
    token_hash = hash_session_token(raw_token)

    session = db_session.execute(select(UserSession).where(UserSession.token_hash == token_hash)).scalar_one_or_none()
    assert session is not None

    # Compare cookie expiration with session expiration
    diff = abs((cookie_expires_dt - session.expires_at).total_seconds())
    assert diff <= 1.0

    # Check last_login_at
    from app.db.models.user import User
    user = db_session.execute(select(User).where(User.email == "loginuser@example.com")).scalar_one_or_none()
    assert user.last_login_at is not None

def test_login_wrong_password(client, db_session):
    client.post(
        "/v1/auth/register",
        json={"email": "wrongpwd@example.com", "password": "SecurePassword123!"}
    )

    response = client.post(
        "/v1/auth/login",
        json={"email": "wrongpwd@example.com", "password": "wrong"}
    )
    assert response.status_code == 401
    assert "session_token" not in response.cookies
    assert response.json()["detail"] == "Invalid email or password"
    assert "www-authenticate" not in response.headers

def test_login_nonexistent_user(client):
    response = client.post(
        "/v1/auth/login",
        json={"email": "nonexistent@example.com", "password": "SecurePassword123!"}
    )
    assert response.status_code == 401
    assert "session_token" not in response.cookies
    assert response.json()["detail"] == "Invalid email or password"
    assert "www-authenticate" not in response.headers

def test_login_inactive_user(client, db_session):
    client.post(
        "/v1/auth/register",
        json={"email": "inactive@example.com", "password": "SecurePassword123!"}
    )

    from app.db.models.user import User
    from sqlalchemy import select
    user = db_session.execute(select(User).where(User.email == "inactive@example.com")).scalar_one()
    user.is_active = False
    db_session.commit()

    response = client.post(
        "/v1/auth/login",
        json={"email": "inactive@example.com", "password": "SecurePassword123!"}
    )
    assert response.status_code == 401
    assert "session_token" not in response.cookies
    assert response.json()["detail"] == "Invalid email or password"
    assert "www-authenticate" not in response.headers

def test_logout_success(client, db_session):
    client.post("/v1/auth/register", json={"email": "logout@example.com", "password": "SecurePassword123!"})
    login_res = client.post("/v1/auth/login", json={"email": "logout@example.com", "password": "SecurePassword123!"})
    assert login_res.status_code == 200

    # We need to pass the cookie manually if TestClient is over HTTP,
    # but let's see if the test client passes it correctly or we need to manually pass it.
    # To be robust, let's explicitly set the cookie for logout.
    raw_token = login_res.cookies.get("session_token")
    if not raw_token:
        # Fallback to parse it manually if it wasn't captured due to Secure=True
        set_cookie_header = login_res.headers.get("set-cookie")
        import email.utils
        parts = set_cookie_header.split(";")
        raw_token = parts[0].split("=")[1].strip()

    client.cookies.set("session_token", raw_token)

    logout_res = client.post("/v1/auth/logout")
    assert logout_res.status_code == 204

    # Verify cookie clearing
    set_cookie_header = logout_res.headers.get("set-cookie", "")
    assert "session_token=" in set_cookie_header
    # Some frameworks set max-age=0 or expires in the past for deletion
    assert 'max-age=0' in set_cookie_header.lower() or 'expires=' in set_cookie_header.lower()

    # Verify DB revoked
    from app.db.models.user_session import UserSession
    from app.core.security import hash_session_token
    from sqlalchemy import select
    token_hash = hash_session_token(raw_token)
    session = db_session.execute(select(UserSession).where(UserSession.token_hash == token_hash)).scalar_one_or_none()
    assert session.revoked_at is not None

    # Verify /v1/me rejects the same cookie
    client.cookies.set("session_token", raw_token)
    me_res = client.get("/v1/me")
    assert me_res.status_code == 401

def test_logout_missing_cookie(client):
    client.cookies.clear()
    logout_res = client.post("/v1/auth/logout")
    assert logout_res.status_code == 204
    # The endpoint should still try to clear the cookie
    assert "session_token=" in logout_res.headers.get("set-cookie", "")

def test_logout_invalid_cookie(client):
    client.cookies.set("session_token", "invalidtoken")
    logout_res = client.post("/v1/auth/logout")
    assert logout_res.status_code == 204
    assert "session_token=" in logout_res.headers.get("set-cookie", "")

def test_logout_repeated(client, db_session):
    client.post("/v1/auth/register", json={"email": "logout2@example.com", "password": "SecurePassword123!"})
    login_res = client.post("/v1/auth/login", json={"email": "logout2@example.com", "password": "SecurePassword123!"})

    set_cookie_header = login_res.headers.get("set-cookie")
    raw_token = set_cookie_header.split(";")[0].split("=")[1].strip()
    client.cookies.set("session_token", raw_token)

    # First logout
    res1 = client.post("/v1/auth/logout")
    assert res1.status_code == 204

    # Restore cookie in client for second logout
    client.cookies.set("session_token", raw_token)

    # Second logout (idempotent)
    res2 = client.post("/v1/auth/logout")
    assert res2.status_code == 204

def test_logout_does_not_revoke_other_user(client, db_session):
    # User A
    client.post("/v1/auth/register", json={"email": "userA@example.com", "password": "SecurePassword123!"})
    loginA = client.post("/v1/auth/login", json={"email": "userA@example.com", "password": "SecurePassword123!"})
    tokenA = loginA.headers.get("set-cookie").split(";")[0].split("=")[1].strip()

    # User B
    client.post("/v1/auth/register", json={"email": "userB@example.com", "password": "SecurePassword123!"})
    loginB = client.post("/v1/auth/login", json={"email": "userB@example.com", "password": "SecurePassword123!"})
    tokenB = loginB.headers.get("set-cookie").split(";")[0].split("=")[1].strip()

    # Logout User A
    client.cookies.set("session_token", tokenA)
    client.post("/v1/auth/logout")

    from app.db.models.user_session import UserSession
    from app.core.security import hash_session_token
    from sqlalchemy import select

    # Assert A revoked, B active
    sessionA = db_session.execute(select(UserSession).where(UserSession.token_hash == hash_session_token(tokenA))).scalar_one_or_none()
    sessionB = db_session.execute(select(UserSession).where(UserSession.token_hash == hash_session_token(tokenB))).scalar_one_or_none()

    assert sessionA.revoked_at is not None
    assert sessionB.revoked_at is None

def test_logout_unexpected_error(client, db_session, monkeypatch):
    client.post("/v1/auth/register", json={"email": "logouterr@example.com", "password": "SecurePassword123!"})
    login_res = client.post("/v1/auth/login", json={"email": "logouterr@example.com", "password": "SecurePassword123!"})

    set_cookie_header = login_res.headers.get("set-cookie")
    raw_token = set_cookie_header.split(";")[0].split("=")[1].strip()
    client.cookies.set("session_token", raw_token)

    def mock_revoke_session(*args, **kwargs):
        raise Exception("Simulated database failure")

    from app.services import auth_service
    monkeypatch.setattr(auth_service, "revoke_session", mock_revoke_session)

    logout_res = client.post("/v1/auth/logout")

    # Prove it returns 500, not 204
    assert logout_res.status_code == 500
    # Prove it uses the generic 500 contract without leaking the exception details
    assert logout_res.json()["detail"] == "Internal server error"
    assert "Simulated database failure" not in logout_res.text
