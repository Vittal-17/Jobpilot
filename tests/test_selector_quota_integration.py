from datetime import datetime, timezone

from sqlalchemy import text

from app.core.config import settings
from app.providers.types import ProviderName
from app.services.provider_router import get_provider_capacity


def _set_usage(db_session, minute_count=0, daily_count=0):
    now = datetime.now(timezone.utc)
    minute = now.replace(second=0, microsecond=0)
    if minute_count:
        db_session.execute(
            text(
                "INSERT INTO provider_minute_usage "
                "(provider_name, usage_minute, request_count) "
                "VALUES ('adzuna', :minute, :count)"
            ),
            {"minute": minute, "count": minute_count},
        )
    if daily_count:
        db_session.execute(
            text(
                "INSERT INTO provider_usage "
                "(provider_name, usage_date, request_count) "
                "VALUES ('adzuna', :day, :count)"
            ),
            {"day": now.date(), "count": daily_count},
        )
    db_session.commit()
    return now


def test_selector_reads_usage_without_mutating_quota(db_session, monkeypatch):
    monkeypatch.setattr(settings, "adzuna_safety_budget_daily", 25)
    now = _set_usage(db_session, minute_count=3, daily_count=4)

    before = (
        db_session.execute(text("SELECT SUM(request_count) FROM provider_minute_usage")).scalar(),
        db_session.execute(text("SELECT SUM(request_count) FROM provider_usage")).scalar(),
        db_session.execute(text("SELECT SUM(lifetime_count) FROM provider_state")).scalar(),
    )
    assert get_provider_capacity(db_session, ProviderName.ADZUNA, now).available is True
    after = (
        db_session.execute(text("SELECT SUM(request_count) FROM provider_minute_usage")).scalar(),
        db_session.execute(text("SELECT SUM(request_count) FROM provider_usage")).scalar(),
        db_session.execute(text("SELECT SUM(lifetime_count) FROM provider_state")).scalar(),
    )
    assert after == before


def test_selector_blocks_actual_minute_usage_at_limit(db_session):
    now = _set_usage(db_session, minute_count=25)
    assert get_provider_capacity(db_session, ProviderName.ADZUNA, now).available is False


def test_selector_blocks_actual_daily_usage_at_effective_limit(db_session, monkeypatch):
    monkeypatch.setattr(settings, "adzuna_safety_budget_daily", 2)
    now = _set_usage(db_session, daily_count=2)
    assert get_provider_capacity(db_session, ProviderName.ADZUNA, now).available is False


def test_selector_blocks_actual_lifetime_usage_at_limit(db_session, monkeypatch):
    monkeypatch.setattr(settings, "jooble_safety_budget_daily", 10)
    monkeypatch.setattr(settings, "jooble_safety_budget_lifetime", 3)
    db_session.execute(
        text(
            "INSERT INTO provider_state (provider_name, lifetime_count) "
            "VALUES ('jooble', 3)"
        )
    )
    db_session.commit()
    assert get_provider_capacity(db_session, ProviderName.JOOBLE).available is False
