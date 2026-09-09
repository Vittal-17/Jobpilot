import pytest
from unittest.mock import MagicMock, call
from app.services.ingestion import acquire_provider_request_slot
from app.services.ingestion import DatabaseUnavailable

def get_db_mock(scalar_returns):
    db_mock = MagicMock()
    # Create a mock for the result of execute
    result_mock = MagicMock()
    result_mock.scalar.side_effect = scalar_returns
    db_mock.execute.return_value = result_mock
    return db_mock

def test_acquire_provider_request_slot_success():
    # A. Successful reservation
    # For Adzuna: minute passes (1), daily passes (1), lifetime is skipped
    db_mock = get_db_mock([1, 1])
    result = acquire_provider_request_slot(db_mock, "adzuna")
    assert result is True
    assert db_mock.commit.called
    assert not db_mock.rollback.called
    assert db_mock.execute.call_count == 2

def test_acquire_provider_request_slot_minute_limit_exceeded():
    # B. Minute limit exceeded
    # Adzuna: minute fails (return 26).
    db_mock = get_db_mock([26])
    result = acquire_provider_request_slot(db_mock, "adzuna")
    assert result is False
    assert db_mock.rollback.called
    assert not db_mock.commit.called
    # Only minute SQL should execute
    assert db_mock.execute.call_count == 1

def test_acquire_provider_request_slot_daily_limit_exceeded():
    # C. Daily limit exceeded
    # Adzuna: minute passes (1), daily fails (return 26).
    db_mock = get_db_mock([1, 26])
    result = acquire_provider_request_slot(db_mock, "adzuna")
    assert result is False
    assert db_mock.rollback.called
    assert not db_mock.commit.called
    # Minute and daily executed
    assert db_mock.execute.call_count == 2

def test_acquire_provider_request_slot_lifetime_limit_exceeded():
    # D. Lifetime limit exceeded
    # Jooble: daily passes (1), lifetime fails (return 501)
    db_mock = get_db_mock([1, 501])
    result = acquire_provider_request_slot(db_mock, "jooble")
    assert result is False
    assert db_mock.rollback.called
    assert not db_mock.commit.called
    # Daily and lifetime executed
    assert db_mock.execute.call_count == 2

def test_provider_with_no_lifetime_limit():
    # E. Provider with no lifetime limit
    # Adzuna has no lifetime limit. Returns minute=1, daily=1.
    db_mock = get_db_mock([1, 1])
    result = acquire_provider_request_slot(db_mock, "adzuna")
    assert result is True
    # Verify execute called twice (minute, daily) - no lifetime
    assert db_mock.execute.call_count == 2

def test_provider_with_no_minute_limit():
    # F. Provider with no minute limit
    # Jooble has no minute limit. Returns daily=1, lifetime=1.
    db_mock = get_db_mock([1, 1])
    result = acquire_provider_request_slot(db_mock, "jooble")
    assert result is True
    # Verify execute called twice (daily, lifetime) - no minute
    assert db_mock.execute.call_count == 2

def test_acquire_provider_request_slot_db_failure():
    # G. Database failure
    db_mock = MagicMock()
    db_mock.execute.side_effect = Exception("DB Error")
    with pytest.raises(DatabaseUnavailable):
        acquire_provider_request_slot(db_mock, "adzuna")
    assert db_mock.rollback.called
    assert not db_mock.commit.called

def test_acquire_provider_request_slot_unknown_provider():
    # H. Unknown provider
    db_mock = MagicMock()
    with pytest.raises(ValueError, match="ConfigurationError: Unknown provider"):
        acquire_provider_request_slot(db_mock, "unknown_provider")
    # Doesn't even reach DB
    assert not db_mock.execute.called
