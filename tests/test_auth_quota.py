import httpx
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db.database import get_db
from app.db.models.provider_usage import ProviderUsageModel
from app.core.config import settings
import respx

client = TestClient(app)

def test_auth_matrix_and_quota_side_effects(db_session, monkeypatch):
    app.dependency_overrides[get_db] = lambda: db_session
    test_secret = "test_secret_12345"
    monkeypatch.setattr(settings, "api_secret_key", test_secret)
    monkeypatch.setattr(settings, "adzuna_safety_budget_daily", 10)
    monkeypatch.setattr(settings, "adzuna_app_id", "A")
    monkeypatch.setattr(settings, "adzuna_app_key", "B")

    payload = {"keywords": "py", "location": "blr"}

    try:
        with respx.mock:
            # Missing key -> 401
            assert client.post("/ingestion/adzuna", json=payload).status_code == 401

            # Empty key -> 401
            assert client.post("/ingestion/adzuna", headers={"X-Api-Key": ""}, json=payload).status_code == 401

            # Wrong key -> 401
            assert client.post("/ingestion/adzuna", headers={"X-Api-Key": "wrong"}, json=payload).status_code == 401

            # Malformed key (unicode/spaces) -> 401
            assert client.post("/ingestion/adzuna", headers={"X-Api-Key": "test secret "}, json=payload).status_code == 401

            # Verify no provider requests made, no quota consumed
            usage = db_session.query(ProviderUsageModel).filter_by(provider_name="adzuna").first()
            assert usage is None or usage.request_count == 0

            # Correct key -> authenticated path proceeds (hits mock provider response)
            respx.get("https://api.adzuna.com/v1/api/jobs/in/search/1").mock(return_value=httpx.Response(200, json={"results": []}))
            res = client.post("/ingestion/adzuna", headers={"X-Api-Key": settings.api_secret_key}, json=payload)
            assert res.status_code == 200

            # Now quota is consumed
            usage = db_session.query(ProviderUsageModel).filter_by(provider_name="adzuna").first()
            assert usage is not None and usage.request_count == 1
    finally:
        app.dependency_overrides.clear()
