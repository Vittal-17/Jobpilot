import pytest
from app.main import app
from fastapi.testclient import TestClient
from app.db.database import get_db
from app.db.models.provider_usage import ProviderUsageModel
from app.db.models.provider_state import ProviderStateModel
from app.core.config import settings
import respx
import httpx

client = TestClient(app)

def test_missing_jooble_config_does_not_consume_quota(db_session, monkeypatch):
    app.dependency_overrides[get_db] = lambda: db_session
    test_secret = "test_secret_12345"
    monkeypatch.setattr(settings, "api_secret_key", test_secret)
    monkeypatch.setattr(settings, "jooble_api_key", "") # Missing!

    payload = {"keywords": "py", "location": "blr"}

    try:
        with respx.mock:
            # The API route traps ProviderConfigurationError and returns 500
            res = client.post("/ingestion/jooble", headers={"X-Api-Key": settings.api_secret_key}, json=payload)
            assert res.status_code == 500
            assert "configuration error" in res.json()["detail"].lower()

            # Verify no quota consumed
            daily = db_session.query(ProviderUsageModel).filter_by(provider_name="jooble").first()
            assert daily is None or daily.request_count == 0

            lifetime = db_session.query(ProviderStateModel).filter_by(provider_name="jooble").first()
            assert lifetime is None or lifetime.lifetime_count == 0

    finally:
        app.dependency_overrides.clear()
