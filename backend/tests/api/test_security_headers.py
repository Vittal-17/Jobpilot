import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_security_headers():
    response = client.get("/health")
    assert response.status_code == 200

    headers = response.headers
    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert headers.get("X-Frame-Options") == "DENY"
    assert headers.get("Strict-Transport-Security") == "max-age=31536000; includeSubDomains"

    csp = headers.get("Content-Security-Policy")
    assert csp is not None
    assert "default-src 'self'" in csp
    assert "script-src 'self' 'unsafe-inline' cdn.jsdelivr.net" in csp
    assert "style-src 'self' 'unsafe-inline' cdn.jsdelivr.net" in csp
    assert "img-src 'self' data: fastapi.tiangolo.com" in csp
