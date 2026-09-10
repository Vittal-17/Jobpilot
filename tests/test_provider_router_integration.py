from datetime import datetime, timezone

from sqlalchemy import text

from app.core.config import settings
from app.providers.types import ProviderName
from app.services.provider_router import get_provider_capacity, route_provider


def test_postgres_quota_reads_do_not_mutate_usage(db_session, monkeypatch):
    monkeypatch.setattr(settings, "adzuna_app_id", "configured")
    monkeypatch.setattr(settings, "adzuna_app_key", "configured")
    monkeypatch.setattr(settings, "jooble_api_key", "configured")
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
    assert get_provider_capacity(db_session, ProviderName.ADZUNA, now).daily_remaining == 24


def test_postgres_exhausted_adzuna_routes_jooble(db_session, monkeypatch):
    monkeypatch.setattr(settings, "adzuna_app_id", "configured")
    monkeypatch.setattr(settings, "adzuna_app_key", "configured")
    monkeypatch.setattr(settings, "jooble_api_key", "configured")
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
