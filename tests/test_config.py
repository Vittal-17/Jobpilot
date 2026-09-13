import pytest
from app.core.config import Settings
import os

def test_production_secret_missing(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("API_SECRET_KEY", raising=False)
    with pytest.raises(ValueError):
        Settings(_env_file=None)

def test_production_secret_empty(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("API_SECRET_KEY", "")
    with pytest.raises(ValueError, match="empty or whitespace"):
        Settings(_env_file=None)

def test_production_secret_whitespace(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("API_SECRET_KEY", "                ")
    with pytest.raises(ValueError, match="whitespace"):
        Settings(_env_file=None)

def test_production_secret_dev_key(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("API_SECRET_KEY", "dev_secret_key_1234567")
    with pytest.raises(ValueError, match="unsafe"):
        Settings(_env_file=None)

def test_production_secret_placeholder(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("API_SECRET_KEY", "your_strong_internal_api_secret_key_here")
    with pytest.raises(ValueError, match="unsafe"):
        Settings(_env_file=None)

def test_production_secret_valid(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("API_SECRET_KEY", "a_very_strong_random_secret_string")
    settings = Settings(_env_file=None)
    assert settings.api_secret_key == "a_very_strong_random_secret_string"

def test_development_secret_valid(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("API_SECRET_KEY", "dev")
    settings = Settings(_env_file=None)
    assert settings.api_secret_key == "dev"
