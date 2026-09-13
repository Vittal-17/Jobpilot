import pytest
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.services.ingestion import acquire_provider_request_slot
from app.services.search_selector import _clean_abandoned_claims

from unittest.mock import patch
from app.services.quota_policy import ProviderQuotaPolicy, QuotaDimensions

def test_acquire_provider_request_slot_isolation(db_session: Session):
    db_session.execute(
        text("INSERT INTO provider_state (provider_name, lifetime_count) VALUES ('test_isolation', 0)")
    )

    mock_policy = ProviderQuotaPolicy(
        provider_ceiling=QuotaDimensions(lifetime=100),
        account_ceiling=QuotaDimensions(lifetime=100),
        safety_budget=QuotaDimensions(lifetime=10)
    )

    with patch('app.services.quota_policy.get_provider_policy', return_value=mock_policy):
        result = acquire_provider_request_slot(db_session, "test_isolation_quota")

    assert result is True

    # If we rollback our outer session...
    db_session.rollback()

    # The dummy row should be gone
    assert db_session.execute(
        text("SELECT COUNT(*) FROM provider_state WHERE provider_name = 'test_isolation'")
    ).scalar_one() == 0

    # But the quota increment should STILL be there because it committed independently!
    assert db_session.execute(
        text("SELECT COUNT(*) FROM provider_state WHERE provider_name = 'test_isolation_quota'")
    ).scalar_one() == 1

def test_clean_abandoned_claims_isolation(db_session: Session):
    db_session.execute(
        text("INSERT INTO provider_state (provider_name, lifetime_count) VALUES ('test_clean_iso', 0)")
    )

    # Insert an abandoned claim independently so it's already there
    engine = db_session.get_bind()
    with Session(engine) as setup_db:
        setup_db.execute(
            text("INSERT INTO search_execution (candidate_id, status, selected_at) VALUES ('dummy_cand', 'selected', '2000-01-01 00:00:00')")
        )
        setup_db.commit()

    _clean_abandoned_claims(db_session)

    db_session.rollback()

    assert db_session.execute(
        text("SELECT COUNT(*) FROM provider_state WHERE provider_name = 'test_clean_iso'")
    ).scalar_one() == 0

    # The abandoned claim should be marked failed (committed independently)
    assert db_session.execute(
        text("SELECT COUNT(*) FROM search_execution WHERE candidate_id = 'dummy_cand' AND status = 'failed'")
    ).scalar_one() == 1
