import pytest
from app.services.ingestion import acquire_provider_request_slot
from app.core.config import settings
import concurrent.futures
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
import logging

def test_concurrent_quota(engine, monkeypatch, caplog):
    caplog.set_level(logging.ERROR)
    monkeypatch.setattr(settings, "jooble_safety_budget_daily", 5)

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

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(lambda _: worker(), range(10)))

    successes = sum(1 for r in results if r)
    assert successes == 5

    # Blocker 4: Assert exact daily counter state
    s = SessionLocal()
    try:
        stmt = select(provider_usage.ProviderUsageModel.request_count).where(provider_usage.ProviderUsageModel.provider_name == "jooble")
        count = s.execute(stmt).scalar()
        assert count == 5
    finally:
        s.close()
