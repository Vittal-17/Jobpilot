from datetime import datetime, timezone

from sqlalchemy import text

from app.core.config import settings
from app.providers.types import ProviderName
from app.services.provider_router import get_provider_capacity, route_provider
import app.db.models  # noqa: F401


def test_postgres_quota_reads_do_not_mutate_usage(db_session, monkeypatch):
    monkeypatch.setattr(settings, "adzuna_app_id", "configured")
    monkeypatch.setattr(settings, "adzuna_app_key", "configured")
    monkeypatch.setattr(settings, "jooble_in_api_key", "configured")
    now = datetime.now(timezone.utc)
    db_session.execute(
        text(
            "INSERT INTO provider_usage (provider_name, usage_date, request_count) "
            "VALUES ('adzuna', :day, 1), ('jooble', :day, 1)"
        ),
        {"day": now.date()},
    )
    db_session.commit()
    before = db_session.execute(
        text("SELECT provider_name, request_count FROM provider_usage ORDER BY provider_name")
    ).all()

    decision = route_provider(db_session, now)

    after = db_session.execute(
        text("SELECT provider_name, request_count FROM provider_usage ORDER BY provider_name")
    ).all()
    assert decision.provider == ProviderName.ADZUNA
    assert after == before
    cap = get_provider_capacity(db_session, ProviderName.ADZUNA, now)
    assert cap.daily_remaining == 24
    assert cap.weekly_remaining == 999
    assert cap.monthly_remaining == 2499


def test_postgres_exhausted_adzuna_routes_jooble(db_session, monkeypatch):
    monkeypatch.setattr(settings, "adzuna_app_id", "configured")
    monkeypatch.setattr(settings, "adzuna_app_key", "configured")
    monkeypatch.setattr(settings, "jooble_in_api_key", "configured")
    monkeypatch.setattr(settings, "adzuna_safety_budget_daily", 1)
    now = datetime.now(timezone.utc)
    db_session.execute(
        text(
            "INSERT INTO provider_usage (provider_name, usage_date, request_count) "
            "VALUES ('adzuna', :day, 1)"
        ),
        {"day": now.date()},
    )
    db_session.commit()
    assert route_provider(db_session, now).provider == ProviderName.JOOBLE


def test_postgres_exhausted_weekly_adzuna_routes_jooble(db_session, monkeypatch):
    monkeypatch.setattr(settings, "adzuna_app_id", "configured")
    monkeypatch.setattr(settings, "adzuna_app_key", "configured")
    monkeypatch.setattr(settings, "jooble_in_api_key", "configured")
    # Wednesday 2026-03-18 (Week: Mon 2026-03-16 to Sun 2026-03-22)
    now = datetime(2026, 3, 18, 12, 0, tzinfo=timezone.utc)
    db_session.execute(
        text(
            "INSERT INTO provider_usage (provider_name, usage_date, request_count) VALUES "
            "('adzuna', '2026-03-16', 500), "
            "('adzuna', '2026-03-17', 495), "
            "('adzuna', '2026-03-18', 5)"
        )
    )
    db_session.commit()

    cap = get_provider_capacity(db_session, ProviderName.ADZUNA, now)
    # Daily remaining is 25 - 5 = 20 > 0
    assert cap.daily_remaining == 20
    # Weekly limit is 1000, usage is 500 + 495 + 5 = 1000 -> 0 remaining
    assert cap.weekly_remaining == 0
    assert cap.available is False

    assert route_provider(db_session, now).provider == ProviderName.JOOBLE


def test_postgres_exhausted_monthly_adzuna_routes_jooble(db_session, monkeypatch):
    monkeypatch.setattr(settings, "adzuna_app_id", "configured")
    monkeypatch.setattr(settings, "adzuna_app_key", "configured")
    monkeypatch.setattr(settings, "jooble_in_api_key", "configured")
    # Wednesday 2026-03-18
    now = datetime(2026, 3, 18, 12, 0, tzinfo=timezone.utc)
    # Earlier in March (week of 2026-03-02 and 2026-03-09): 2450 requests
    # Current week (2026-03-18): 50 requests
    db_session.execute(
        text(
            "INSERT INTO provider_usage (provider_name, usage_date, request_count) VALUES "
            "('adzuna', '2026-03-03', 1200), "
            "('adzuna', '2026-03-10', 1250), "
            "('adzuna', '2026-03-18', 50)"
        )
    )
    db_session.commit()

    cap = get_provider_capacity(db_session, ProviderName.ADZUNA, now)
    # Weekly remaining is 1000 - 50 = 950 > 0
    assert cap.weekly_remaining == 950
    # Monthly limit is 2500, usage is 1200 + 1250 + 50 = 2500 -> 0 remaining
    assert cap.monthly_remaining == 0
    assert cap.available is False

    assert route_provider(db_session, now).provider == ProviderName.JOOBLE


def test_postgres_usage_outside_current_week_and_month_does_not_exhaust(db_session, monkeypatch):
    monkeypatch.setattr(settings, "adzuna_app_id", "configured")
    monkeypatch.setattr(settings, "adzuna_app_key", "configured")
    monkeypatch.setattr(settings, "jooble_in_api_key", "configured")
    # Wednesday 2026-03-18
    now = datetime(2026, 3, 18, 12, 0, tzinfo=timezone.utc)
    # Usage in February (previous month): 2500 requests
    db_session.execute(
        text(
            "INSERT INTO provider_usage (provider_name, usage_date, request_count) "
            "VALUES ('adzuna', '2026-02-15', 2500)"
        )
    )
    db_session.commit()

    cap = get_provider_capacity(db_session, ProviderName.ADZUNA, now)
    assert cap.weekly_remaining == 1000
    assert cap.monthly_remaining == 2500
    assert cap.available is True
    assert route_provider(db_session, now).provider == ProviderName.ADZUNA
