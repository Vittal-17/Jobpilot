import pytest
from datetime import datetime, timezone
import concurrent.futures
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.db.database import SessionLocal
from app.core.config import settings
from app.services.ingestion import acquire_provider_request_slot
from app.services.firecrawl_quota import get_firecrawl_budget_state


def clear_firecrawl_usage(db):
    db.execute(text("DELETE FROM provider_usage WHERE provider_name IN ('firecrawl', 'firecrawl_monthly')"))
    db.commit()


@pytest.fixture(autouse=True)
def setup_firecrawl_env():
    orig_key = settings.firecrawl_api_key
    orig_enabled = settings.firecrawl_enabled
    orig_budget = settings.firecrawl_monthly_budget
    orig_cap = settings.firecrawl_monthly_automation_cap
    orig_reserve = settings.firecrawl_reserved_credits

    settings.firecrawl_api_key = "test_key"
    settings.firecrawl_enabled = True
    settings.firecrawl_monthly_budget = 1000
    settings.firecrawl_monthly_automation_cap = 900
    settings.firecrawl_reserved_credits = 100

    db = SessionLocal()
    clear_firecrawl_usage(db)
    db.close()

    yield

    db = SessionLocal()
    clear_firecrawl_usage(db)
    db.close()

    settings.firecrawl_api_key = orig_key
    settings.firecrawl_enabled = orig_enabled
    settings.firecrawl_monthly_budget = orig_budget
    settings.firecrawl_monthly_automation_cap = orig_cap
    settings.firecrawl_reserved_credits = orig_reserve


def test_variable_cost_units_reservation():
    db = SessionLocal()
    try:
        # 1 unit for scrape
        assert acquire_provider_request_slot(db, "firecrawl", cost_units=1) is True
        # 2 units for search query
        assert acquire_provider_request_slot(db, "firecrawl", cost_units=2) is True
        # 5 units for batch
        assert acquire_provider_request_slot(db, "firecrawl", cost_units=5) is True

        state = get_firecrawl_budget_state(db)
        assert state["firecrawl_discovery_used"] == 8
        assert state["firecrawl_monthly_used"] == 8
        assert state["firecrawl_monthly_remaining"] == 900 - 8
    finally:
        db.close()


def test_invalid_and_non_positive_cost_units():
    db = SessionLocal()
    try:
        assert acquire_provider_request_slot(db, "firecrawl", cost_units=0) is False
        assert acquire_provider_request_slot(db, "firecrawl", cost_units=-1) is False
        assert acquire_provider_request_slot(db, "firecrawl", cost_units=-10) is False

        # Verify nothing written to DB
        state = get_firecrawl_budget_state(db)
        assert state["firecrawl_monthly_used"] == 0
    finally:
        db.close()


def test_disabled_firecrawl_reservation():
    db = SessionLocal()
    try:
        settings.firecrawl_enabled = False
        assert acquire_provider_request_slot(db, "firecrawl", cost_units=1) is False
        assert acquire_provider_request_slot(db, "firecrawl_monthly", cost_units=1) is False

        settings.firecrawl_enabled = True
        settings.firecrawl_api_key = None
        assert acquire_provider_request_slot(db, "firecrawl", cost_units=1) is False

        settings.firecrawl_api_key = ""
        assert acquire_provider_request_slot(db, "firecrawl", cost_units=1) is False
    finally:
        db.close()


def test_exact_hard_cap_boundary_900():
    db = SessionLocal()
    try:
        today = datetime.now(timezone.utc).date()
        # Seed 898 credits: 500 discovery, 398 enrichment
        db.execute(text("""
            INSERT INTO provider_usage (provider_name, usage_date, request_count)
            VALUES ('firecrawl', :d, 500), ('firecrawl_monthly', DATE_TRUNC('month', :d)::DATE, 398)
        """), {"d": today})
        db.commit()

        # Reserving 2 credits reaches exact boundary 900
        assert acquire_provider_request_slot(db, "firecrawl", cost_units=2) is True

        state = get_firecrawl_budget_state(db)
        assert state["firecrawl_monthly_used"] == 900
        assert state["firecrawl_monthly_remaining"] == 0
        assert state["is_exhausted"] is True

        # Any further reservation is blocked
        assert acquire_provider_request_slot(db, "firecrawl", cost_units=1) is False
        assert acquire_provider_request_slot(db, "firecrawl_monthly", cost_units=1) is False
    finally:
        db.close()


def test_overshoot_reservation_rejection_no_partial_leak():
    db = SessionLocal()
    try:
        today = datetime.now(timezone.utc).date()
        # Seed 899 credits
        db.execute(text("""
            INSERT INTO provider_usage (provider_name, usage_date, request_count)
            VALUES ('firecrawl', :d, 899)
        """), {"d": today})
        db.commit()

        # Reserving 2 credits would yield 901 > 900 -> blocked
        assert acquire_provider_request_slot(db, "firecrawl", cost_units=2) is False

        # Usage must remain 899 without leakage
        state = get_firecrawl_budget_state(db)
        assert state["firecrawl_monthly_used"] == 899

        # Reserving 1 credit fits exactly
        assert acquire_provider_request_slot(db, "firecrawl", cost_units=1) is True
        state = get_firecrawl_budget_state(db)
        assert state["firecrawl_monthly_used"] == 900
    finally:
        db.close()


def test_protected_100_credit_reserve_never_breached():
    db = SessionLocal()
    try:
        today = datetime.now(timezone.utc).date()
        settings.firecrawl_monthly_budget = 1000
        settings.firecrawl_monthly_automation_cap = 900
        settings.firecrawl_reserved_credits = 100

        # Seed 900 credits
        db.execute(text("""
            INSERT INTO provider_usage (provider_name, usage_date, request_count)
            VALUES ('firecrawl', :d, 900)
        """), {"d": today})
        db.commit()

        # Automated reservation blocked even though account budget is 1000
        assert acquire_provider_request_slot(db, "firecrawl", cost_units=1) is False
        assert acquire_provider_request_slot(db, "firecrawl_monthly", cost_units=1) is False

        # State confirms 100 reserve protected
        state = get_firecrawl_budget_state(db)
        assert state["protected_reserve"] == 100
        assert state["account_budget"] == 1000
        assert state["firecrawl_monthly_remaining"] == 0
    finally:
        db.close()


def test_shared_pool_discovery_and_enrichment():
    db = SessionLocal()
    try:
        # Discovery spends 10
        assert acquire_provider_request_slot(db, "firecrawl", cost_units=10) is True
        # Enrichment spends 5
        assert acquire_provider_request_slot(db, "firecrawl_monthly", cost_units=5) is True

        state = get_firecrawl_budget_state(db)
        assert state["firecrawl_discovery_used"] == 10
        assert state["firecrawl_enrichment_used"] == 5
        assert state["firecrawl_monthly_used"] == 15
        assert state["firecrawl_monthly_remaining"] == 885
    finally:
        db.close()


def test_month_rollover():
    db = SessionLocal()
    try:
        # Insert 900 credits in previous month
        db.execute(text("""
            INSERT INTO provider_usage (provider_name, usage_date, request_count)
            VALUES ('firecrawl', DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month')::DATE, 900)
        """))
        db.commit()

        # Reservation in current month must succeed
        assert acquire_provider_request_slot(db, "firecrawl", cost_units=2) is True

        state = get_firecrawl_budget_state(db)
        assert state["firecrawl_monthly_used"] == 2
        assert state["firecrawl_monthly_remaining"] == 898
    finally:
        db.close()


def test_concurrent_advisory_locked_reservations():
    settings.firecrawl_monthly_automation_cap = 50
    settings.firecrawl_monthly_budget = 1000

    def worker_attempt():
        db = SessionLocal()
        try:
            return acquire_provider_request_slot(db, "firecrawl", cost_units=5)
        finally:
            db.close()

    # 20 workers requesting 5 credits each (total 100 requested against 50 cap)
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(lambda _: worker_attempt(), range(20)))

    successes = [r for r in results if r]
    failures = [r for r in results if not r]

    # Exactly 10 succeed (10 * 5 = 50 credits) and 10 fail
    assert len(successes) == 10
    assert len(failures) == 10

    db = SessionLocal()
    try:
        state = get_firecrawl_budget_state(db)
        assert state["firecrawl_monthly_used"] == 50
        assert state["firecrawl_monthly_remaining"] == 0
    finally:
        db.close()


def test_concurrent_mixed_discovery_and_enrichment():
    settings.firecrawl_monthly_automation_cap = 30
    settings.firecrawl_monthly_budget = 1000

    def worker_attempt(provider_name):
        db = SessionLocal()
        try:
            return acquire_provider_request_slot(db, provider_name, cost_units=3)
        finally:
            db.close()

    # 10 discovery (3 credits each) and 10 enrichment (3 credits each) = 60 credits requested against 30 cap
    tasks = ["firecrawl" if i % 2 == 0 else "firecrawl_monthly" for i in range(20)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(worker_attempt, tasks))

    successes = [r for r in results if r]
    failures = [r for r in results if not r]

    # Exactly 10 succeed (total 30 credits) and 10 fail
    assert len(successes) == 10
    assert len(failures) == 10

    db = SessionLocal()
    try:
        state = get_firecrawl_budget_state(db)
        assert state["firecrawl_monthly_used"] == 30
        assert state["firecrawl_monthly_remaining"] == 0
    finally:
        db.close()
