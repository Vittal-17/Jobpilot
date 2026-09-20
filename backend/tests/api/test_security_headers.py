from fastapi.testclient import TestClient
from app.main import app
from app.api.endpoints.ingestion import verify_api_key

client = TestClient(app)

def test_security_headers():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("X-XSS-Protection") == "1; mode=block"
    assert response.headers.get("Strict-Transport-Security") == "max-age=31536000; includeSubDomains"
    csp = response.headers.get("Content-Security-Policy")
    assert csp is not None
    assert "default-src 'self'" in csp
    assert "cdn.jsdelivr.net" in csp
    assert "fastapi.tiangolo.com" in csp
