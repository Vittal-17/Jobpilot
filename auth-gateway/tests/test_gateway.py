import pytest
from fastapi.testclient import TestClient
from main import app, verify_session, sign_session
import time
import base64

client = TestClient(app)

import bcrypt
@pytest.fixture(autouse=True)
def patch_credentials():
    import main
    # Generate deterministic hash for "test_password_override"
    real_hash = bcrypt.hashpw(b'test_password_override', bcrypt.gensalt(4))
    b64_hash = base64.b64encode(real_hash).decode('utf-8')
    old_hash = main.CADDY_ADMIN_HASH_B64
    main.CADDY_ADMIN_HASH_B64 = b64_hash
    yield
    main.CADDY_ADMIN_HASH_B64 = old_hash



@pytest.fixture(autouse=True)
def clean_rate_limit():
    import main
    main.FAILED_ATTEMPTS.clear()
    yield
    main.FAILED_ATTEMPTS.clear()

def test_login_page():
    response = client.get("/login")
    assert response.status_code == 200
    assert "JobPilot Command Center" in response.text
    assert "Access Token" in response.text

def test_verify_no_cookie():
    response = client.get("/verify", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?rd=/"

def test_verify_with_rd():
    response = client.get("/verify", headers={"X-Forwarded-Uri": "/some/safe/path"}, follow_redirects=False)
    assert response.status_code == 303
    assert "/login?rd=/some/safe/path" in response.headers["location"]

def test_verify_with_unsafe_rd():
    response = client.get("/verify", headers={"X-Forwarded-Uri": "//evil.com"}, follow_redirects=False)
    assert response.status_code == 303
    assert "/login?rd=/" in response.headers["location"]

def test_verify_valid_session():
    cookie = sign_session("jobpilot_admin", int(time.time()) + 100)
    client.cookies.set("jobpilot_admin_session", cookie)
    response = client.get("/verify", follow_redirects=False)
    assert response.status_code == 200
    assert response.headers["X-User"] == "jobpilot_admin"
    client.cookies.clear()

def test_verify_expired_session():
    cookie = sign_session("jobpilot_admin", int(time.time()) - 100)
    client.cookies.set("jobpilot_admin_session", cookie)
    response = client.get("/verify", follow_redirects=False)
    assert response.status_code == 303
    client.cookies.clear()

def test_verify_tampered_session():
    cookie = sign_session("jobpilot_admin", int(time.time()) + 100)
    # tamper signature
    parts = cookie.split(".")
    tampered = parts[0] + "." + "a" * len(parts[1])
    client.cookies.set("jobpilot_admin_session", tampered)
    response = client.get("/verify", follow_redirects=False)
    assert response.status_code == 303
    client.cookies.clear()

def test_login_post_invalid():
    response = client.post("/login", data={"username": "admin", "password": "wrongpassword", "rd": "/"}, follow_redirects=False)
    assert response.status_code == 401
    assert "Invalid username or password" in response.text

def test_login_post_valid_and_logout():
    response = client.post("/login", data={"username": "jobpilot_admin", "password": "test_password_override", "rd": "/n8n"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/n8n"
    cookie_header = response.headers.get("set-cookie")
    assert cookie_header is not None
    assert "jobpilot_admin_session" in cookie_header
    assert "HttpOnly" in cookie_header
    assert "Secure" in cookie_header
    assert "samesite=lax" in cookie_header.lower()
    assert "Max-Age=86400" in cookie_header or "max-age=86400" in cookie_header.lower()
    assert "expires=" in cookie_header.lower()
    # Verify it doesn't expire in 1970
    assert "1970" not in cookie_header

    # Test logout
    logout_res = client.post("/logout", data={"rd": "/"}, follow_redirects=False)
    assert logout_res.status_code == 303
    logout_cookie = logout_res.headers.get("set-cookie")
    assert logout_cookie is not None
    # Cookie cleared / expired
    assert "jobpilot_admin_session=""" in logout_cookie or "Max-Age=0" in logout_cookie or 'expires=' in logout_cookie.lower()

def test_rate_limit_and_reset():
    from main import FAILED_ATTEMPTS

    # 4 failed attempts -> 401
    for _ in range(4):
        response = client.post("/login", data={"username": "admin", "password": "wrong", "rd": "/"}, follow_redirects=False)
        assert response.status_code == 401

    # 1 successful login -> 303
    response = client.post("/login", data={"username": "jobpilot_admin", "password": "test_password_override", "rd": "/"}, follow_redirects=False)
    assert response.status_code == 303

    # Verify counter is cleared
    assert "testclient" not in FAILED_ATTEMPTS or len(FAILED_ATTEMPTS["testclient"]) == 0

    # Prove rate limit takes 5 failures again
    for _ in range(5):
        response = client.post("/login", data={"username": "admin", "password": "wrong", "rd": "/"}, follow_redirects=False)
        assert response.status_code == 401

    # 6th attempt -> 429
    response = client.post("/login", data={"username": "admin", "password": "wrong", "rd": "/"}, follow_redirects=False)
    assert response.status_code == 429

def test_credential_loading_success():
    import main
    import base64
    import bcrypt

    # Generate a real hash for 'secret_pass'
    real_hash = bcrypt.hashpw(b'secret_pass', bcrypt.gensalt(4))
    b64_hash = base64.b64encode(real_hash).decode('utf-8')

    # Patch main module config
    old_hash = main.CADDY_ADMIN_HASH_B64
    main.CADDY_ADMIN_HASH_B64 = b64_hash

    # Test verify_password works
    assert main.verify_password('secret_pass') is True
    assert main.verify_password('wrong_pass') is False

    # Restore
    main.CADDY_ADMIN_HASH_B64 = old_hash

def test_credential_loading_malformed():
    import main

    old_hash = main.CADDY_ADMIN_HASH_B64

    # Not base64
    main.CADDY_ADMIN_HASH_B64 = "this_is_not_base64_!@#"
    assert main.verify_password('password') is False

    # Base64 but not a bcrypt hash
    main.CADDY_ADMIN_HASH_B64 = "YmFzZTY0LWJ1dC1ub3QtYmNyeXB0"
    assert main.verify_password('password') is False

    # Empty
    main.CADDY_ADMIN_HASH_B64 = ""
    assert main.verify_password('password') is False

    # Restore
    main.CADDY_ADMIN_HASH_B64 = old_hash
