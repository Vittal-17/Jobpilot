import pytest
import os
import urllib.parse
from tests.conftest import get_test_db_url
from app.core.config import settings

def test_db_url_requires_explicit_env(monkeypatch):
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="FATAL: TEST_DATABASE_URL environment variable is required"):
        get_test_db_url()

def test_db_url_rejects_unsafe_names(monkeypatch):
    unsafe_names = ["postgres", "template1", "prod", "jobpilot"]
    for name in unsafe_names:
        monkeypatch.setenv("TEST_DATABASE_URL", f"postgresql://user:pass@localhost:5432/{name}")
        with pytest.raises(RuntimeError, match="FATAL: Database URL appears to target a non-test database"):
            get_test_db_url()

def test_db_url_accepts_safe_name(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://user:pass@localhost:5432/jobpilot_test")
    assert get_test_db_url() == "postgresql://user:pass@localhost:5432/jobpilot_test"

def test_db_url_must_differ_from_dev(monkeypatch):
    dev_url = settings.get_database_url()

    # Same exact URL
    monkeypatch.setenv("TEST_DATABASE_URL", dev_url)
    with pytest.raises(RuntimeError, match="FATAL: Database URL appears to target a non-test database"):
        get_test_db_url()

    # Same normalized URL but diff creds
    parsed = urllib.parse.urlparse(dev_url)
    same_db_diff_creds = f"postgresql://wrong:creds@{parsed.hostname}:{parsed.port}{parsed.path}"
    monkeypatch.setenv("TEST_DATABASE_URL", same_db_diff_creds)
    with pytest.raises(RuntimeError, match="FATAL: Database URL appears to target a non-test database"):
        get_test_db_url()

    # Valid test url
    monkeypatch.setenv("TEST_DATABASE_URL", f"postgresql://user:pass@{parsed.hostname}:{parsed.port}/jobpilot_test")
    assert get_test_db_url() == f"postgresql://user:pass@{parsed.hostname}:{parsed.port}/jobpilot_test"

    # Missing TEST_DATABASE_URL
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="FATAL: TEST_DATABASE_URL environment variable is required"):
        get_test_db_url()
