import os
import json
import time
import hmac
import hashlib
import base64
import bcrypt
import urllib.parse
from collections import defaultdict
from typing import Optional

from fastapi import FastAPI, Request, Form, Response, Depends, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

# --- Configuration ---
AUTH_SECRET_KEY = os.getenv("AUTH_SECRET_KEY")
if not AUTH_SECRET_KEY:
    raise ValueError("AUTH_SECRET_KEY environment variable is mandatory and must be set.")
CADDY_ADMIN_USER = os.getenv("CADDY_ADMIN_USER", "admin")
CADDY_ADMIN_HASH_B64 = os.getenv("CADDY_ADMIN_HASH_B64", "")

COOKIE_NAME = "jobpilot_admin_session"
SESSION_EXPIRY_SECONDS = 86400  # 24 hours

app = FastAPI(title="JobPilot Auth Gateway", docs_url=None, openapi_url=None)
templates = Jinja2Templates(directory="templates")

# Mount static files if directory exists (for robustness)
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# --- Security Helpers ---
def verify_password(plain_password: str) -> bool:
    if not CADDY_ADMIN_HASH_B64:
        return False
    try:
        decoded_hash = base64.b64decode(CADDY_ADMIN_HASH_B64, validate=True)
        return bcrypt.checkpw(plain_password.encode('utf-8'), decoded_hash)
    except Exception:
        return False


def sign_session(username: str, expires_at: int) -> str:
    payload = json.dumps({"user": username, "exp": expires_at}).encode()
    b64_payload = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = hmac.new(AUTH_SECRET_KEY.encode(), b64_payload.encode(), hashlib.sha256).hexdigest()
    return f"{b64_payload}.{signature}"

def verify_session(cookie_val: str) -> bool:
    if not cookie_val or "." not in cookie_val:
        return False
    try:
        b64_payload, signature = cookie_val.split(".", 1)
        expected_sig = hmac.new(AUTH_SECRET_KEY.encode(), b64_payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return False

        # Add padding back if needed
        pad = len(b64_payload) % 4
        if pad:
            b64_payload += "=" * (4 - pad)

        payload = json.loads(base64.urlsafe_b64decode(b64_payload).decode())
        if payload.get("user") != CADDY_ADMIN_USER:
            return False
        if payload.get("exp", 0) < time.time():
            return False
        return True
    except Exception:
        return False

# --- Rate Limiting ---
# Very simple in-memory rate limiter per IP for failed attempts
FAILED_ATTEMPTS = defaultdict(list)
MAX_FAILURES = 5
BLOCK_TIME_SECONDS = 60

def is_rate_limited(ip: str) -> bool:
    now = time.time()
    # Clean up old attempts
    FAILED_ATTEMPTS[ip] = [t for t in FAILED_ATTEMPTS[ip] if now - t < BLOCK_TIME_SECONDS]
    return len(FAILED_ATTEMPTS[ip]) >= MAX_FAILURES

def record_failed_attempt(ip: str):
    FAILED_ATTEMPTS[ip].append(time.time())

# --- Safe Redirects ---
def is_safe_redirect(url: str) -> bool:
    # Must be a relative path starting with / and not //
    return url.startswith("/") and not url.startswith("//")

# --- Endpoints ---

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, rd: Optional[str] = "/"):
    return templates.TemplateResponse(request=request, name="login.html", context={ "rd": rd, "error": None})

@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    rd: str = Form("/")
):
    client_ip = request.client.host if request.client else "unknown"

    if is_rate_limited(client_ip):
        return templates.TemplateResponse(request, "login.html", {"rd": rd, "error": "Too many attempts. Please try again later."}, status_code=429)

    if username == CADDY_ADMIN_USER and verify_password(password):
        # Prevent open redirect
        safe_rd = rd if is_safe_redirect(rd) else "/"

        # Successful login, redirect
        response = RedirectResponse(url=safe_rd, status_code=303)

        # Generate new session
        expires_at = int(time.time()) + SESSION_EXPIRY_SECONDS
        session_val = sign_session(username, expires_at)

        response.set_cookie(
            key=COOKIE_NAME,
            value=session_val,
            max_age=SESSION_EXPIRY_SECONDS,
            expires=expires_at,
            httponly=True,
            secure=True,
            samesite="lax"
        )
        # Clear failures on success
        if client_ip in FAILED_ATTEMPTS:
            del FAILED_ATTEMPTS[client_ip]

        return response

    # Failed login
    record_failed_attempt(client_ip)
    return templates.TemplateResponse(request, "login.html", {"rd": rd, "error": "Invalid username or password."}, status_code=401)

@app.post("/logout")
async def logout(request: Request, rd: str = "/"):
    safe_rd = rd if is_safe_redirect(rd) else "/"
    response = RedirectResponse(url=safe_rd, status_code=303)
    response.delete_cookie(key=COOKIE_NAME, httponly=True, secure=True, samesite="lax")
    return response

@app.get("/verify")
async def verify_auth(request: Request):
    cookie_val = request.cookies.get(COOKIE_NAME)
    if verify_session(cookie_val):
        return Response(status_code=200, headers={"X-User": CADDY_ADMIN_USER})

    # Not authenticated, return 303 Redirect to login.
    # Caddy's forward_auth sets X-Forwarded-Uri for the original requested path.
    original_uri = request.headers.get("X-Forwarded-Uri", "/")

    if not is_safe_redirect(original_uri):
        original_uri = "/"

    encoded_uri = urllib.parse.quote(original_uri)
    redirect_url = f"/login?rd={encoded_uri}"

    # Return 303 so Caddy forwards the redirect to the client
    return RedirectResponse(url=redirect_url, status_code=303)
