import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db.database import get_db

client = TestClient(app)

def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}

def test_adzuna_endpoint_missing_creds(monkeypatch, db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        from app.core.config import settings
        monkeypatch.setattr(settings, "adzuna_app_id", "")
        res = client.post("/ingestion/adzuna", headers={"X-Api-Key": "your_strong_internal_api_secret_key_here"}, json={"keywords": "py", "location": "blr"})
        assert res.status_code == 500
    finally:
        app.dependency_overrides.clear()


def test_adzuna_endpoint_missing_header():
    res = client.post("/ingestion/adzuna", json={"keywords": "py", "location": "blr"})
    assert res.status_code == 401 # missing header is 422 Unprocessable Entity in FastAPI

def test_adzuna_endpoint_invalid_header():
    res = client.post("/ingestion/adzuna", headers={"X-Api-Key": "wrong_key"}, json={"keywords": "py", "location": "blr"})
    assert res.status_code == 401
