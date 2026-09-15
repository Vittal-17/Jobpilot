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
        res = client.post(
            "/ingestion/adzuna",
            headers={"X-Api-Key": settings.api_secret_key},
            json={"keywords": "py", "location": "blr"},
        )
        assert res.status_code == 500
    finally:
        app.dependency_overrides.clear()


def test_adzuna_endpoint_missing_header():
    res = client.post("/ingestion/adzuna", json={"keywords": "py", "location": "blr"})
    assert res.status_code == 401 # missing header is 422 Unprocessable Entity in FastAPI

def test_adzuna_endpoint_invalid_header():
    res = client.post("/ingestion/adzuna", headers={"X-Api-Key": "wrong_key"}, json={"keywords": "py", "location": "blr"})
    assert res.status_code == 401

def test_readiness_db_reachable():
    res = client.get("/health/ready")
    assert res.status_code == 200
    assert res.json() == {"status": "ready"}

def test_readiness_db_unavailable(monkeypatch, db_session):
    # We monkeypatch engine.connect to raise an exception
    from sqlalchemy.engine import Engine
    def mock_connect(*args, **kwargs):
        raise Exception("DB down")
    monkeypatch.setattr(Engine, "connect", mock_connect)
    res = client.get("/health/ready")
    assert res.status_code == 503
    assert "unavailable" in res.json()["detail"].lower()

def test_ingestion_endpoint_unhandled_exception_logs_structured(monkeypatch, db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        from app.core.config import settings
        import app.api.endpoints.ingestion as ingestion_module
        from unittest.mock import patch

        with patch.object(ingestion_module, 'create_provider', side_effect=ValueError("unexpected crash")), \
             patch.object(ingestion_module.logger, 'exception') as mock_logger:

            res = client.post(
                "/ingestion/adzuna",
                headers={"X-Api-Key": settings.api_secret_key},
                json={"keywords": "py", "location": "blr"},
            )
            assert res.status_code == 500
            assert res.json()["detail"] == "Internal server error"

            # Assert logger.exception was called instead of traceback.print_exc()
            mock_logger.assert_called_once_with("Ingestion endpoint unhandled error")
    finally:
        app.dependency_overrides.clear()

def test_daily_budget_exhaustion_clean_stop(db_session, monkeypatch):
    from app.core.config import settings
    from app.db.models.search_execution import SearchExecutionModel
    from app.services.provider_router import ProviderQuotaExhausted
    from app.services.search_selector import SearchCandidate

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        def mock_generate_candidates(db):
            return [SearchCandidate(
                candidate_id="test_candidate",
                role_id="test",
                role_canonical="Test",
                location_id="test",
                location_canonical="Test",
                priority=1,
                tier=1
            )]
        monkeypatch.setattr("app.services.search_selector.generate_candidates", mock_generate_candidates)

        def mock_route_provider(db):
            raise ProviderQuotaExhausted("Simulated exhaustion")
        monkeypatch.setattr("app.services.provider_router.route_provider", mock_route_provider)

        response = client.post(
            "/ingestion/internal/select-next",
            headers={"X-API-Key": settings.api_secret_key},
            json={"cycle_id": "test_cycle"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["action"] == "stop"
        assert data["reason"] == "daily_provider_budget_exhausted"

        claim = db_session.query(SearchExecutionModel).filter(SearchExecutionModel.cycle_id == "test_cycle").order_by(SearchExecutionModel.id.desc()).first()
        assert claim is not None
        assert claim.status == "failed"
        assert claim.error_message == "daily_provider_budget_exhausted"
    finally:
        app.dependency_overrides.clear()
