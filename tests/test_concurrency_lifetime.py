import pytest
from app.services.ingestion import acquire_provider_request_slot
from app.core.config import settings
import concurrent.futures
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
import logging

def get_lifetime_count(session, provider_state):
    stmt = select(provider_state.ProviderStateModel.lifetime_count).where(provider_state.ProviderStateModel.provider_name == 'jooble')
    return session.execute(stmt).scalar() or 0

def test_concurrent_jooble_lifetime(engine, monkeypatch, caplog):
    caplog.set_level(logging.ERROR)
    monkeypatch.setattr(settings, "jooble_safety_budget_daily", 100)
    monkeypatch.setattr(settings, "jooble_safety_budget_lifetime", 15)

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    s = SessionLocal()
    from app.db.base import Base
    from app.db.models import job, provider_usage, provider_state
    for table in reversed(Base.metadata.sorted_tables):
        s.execute(table.delete())
    s.commit()
    s.close()

    def worker():
        session = SessionLocal()
        try:
            return acquire_provider_request_slot(session, "jooble")
        finally:
            session.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(lambda _: worker(), range(20)))

    successes = sum(1 for r in results if r)
    assert successes == 15
    s = SessionLocal()
    assert get_lifetime_count(s, provider_state) == 15
    s.close()

def test_sequential_lifetime_boundary(engine, monkeypatch):
    monkeypatch.setattr(settings, "jooble_safety_budget_daily", 100)
    monkeypatch.setattr(settings, "jooble_safety_budget_lifetime", 500)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    s = SessionLocal()
    from app.db.base import Base
    from app.db.models import job, provider_usage, provider_state
    for table in reversed(Base.metadata.sorted_tables):
        s.execute(table.delete())

    # Seed db
    s.add(provider_state.ProviderStateModel(provider_name='jooble', lifetime_count=498))
    s.commit()
    s.close()

    s = SessionLocal()
    try:
        assert acquire_provider_request_slot(s, "jooble") is True
        assert acquire_provider_request_slot(s, "jooble") is True
        assert acquire_provider_request_slot(s, "jooble") is False
        assert get_lifetime_count(s, provider_state) == 500
    finally:
        s.close()

def test_concurrent_lifetime_boundary(engine, monkeypatch):
    monkeypatch.setattr(settings, "jooble_safety_budget_daily", 100)
    monkeypatch.setattr(settings, "jooble_safety_budget_lifetime", 500)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    s = SessionLocal()
    from app.db.base import Base
    from app.db.models import job, provider_usage, provider_state
    for table in reversed(Base.metadata.sorted_tables):
        s.execute(table.delete())

    s.add(provider_state.ProviderStateModel(provider_name='jooble', lifetime_count=499))
    s.commit()
    s.close()

    def worker():
        session = SessionLocal()
        try:
            return acquire_provider_request_slot(session, "jooble")
        finally:
            session.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(lambda _: worker(), range(10)))

    successes = sum(1 for r in results if r)
    assert successes == 1

    s = SessionLocal()
    assert get_lifetime_count(s, provider_state) == 500
    s.close()
