import pytest
from sqlalchemy.orm import Session
from app.services.ingestion import acquire_provider_request_slot
from app.core.config import settings

def test_acquire_slot_unknown_provider(db_session: Session):
    # Unknown provider -> ValueError ConfigurationError
    with pytest.raises(ValueError, match="ConfigurationError"):
        acquire_provider_request_slot(db_session, "unknown_provider")

def test_acquire_slot_misspelled_provider(db_session: Session):
    with pytest.raises(ValueError, match="ConfigurationError"):
        acquire_provider_request_slot(db_session, "adzunaa")

def test_acquire_slot_whitespace_provider(db_session: Session):
    with pytest.raises(ValueError, match="ConfigurationError"):
        acquire_provider_request_slot(db_session, " ")

def test_acquire_slot_empty_provider(db_session: Session):
    with pytest.raises(ValueError, match="ConfigurationError"):
        acquire_provider_request_slot(db_session, "")

def test_acquire_slot_supported_provider(db_session: Session, monkeypatch):
    monkeypatch.setattr(settings, "adzuna_safety_budget_daily", 10)
    assert acquire_provider_request_slot(db_session, "adzuna") is True
