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


def test_concurrent_weekly_quota(engine, monkeypatch, caplog):
    caplog.set_level(logging.ERROR)
    from app.services import quota_policy
    original_get_policy = quota_policy.get_provider_policy

    def mock_policy(provider_name):
        p = original_get_policy(provider_name)
        if provider_name == "adzuna":
            p.safety_budget.weekly = 5
        return p

    monkeypatch.setattr("app.services.quota_policy.get_provider_policy", mock_policy)

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    s = SessionLocal()
    from app.db.base import Base
    for table in reversed(Base.metadata.sorted_tables):
        s.execute(table.delete())
    s.commit()
    s.close()

    def worker():
        session = SessionLocal()
        try:
            return acquire_provider_request_slot(session, "adzuna")
        finally:
            session.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(lambda _: worker(), range(10)))

    successes = sum(1 for r in results if r)
    assert successes == 5

    s = SessionLocal()
    try:
        from app.db.models import provider_usage
        stmt = select(provider_usage.ProviderUsageModel.request_count).where(provider_usage.ProviderUsageModel.provider_name == "adzuna")
        count = s.execute(stmt).scalar()
        assert count == 5
    finally:
        s.close()


def test_concurrent_monthly_quota(engine, monkeypatch, caplog):
    caplog.set_level(logging.ERROR)
    from app.services import quota_policy
    original_get_policy = quota_policy.get_provider_policy

    def mock_policy(provider_name):
        p = original_get_policy(provider_name)
        if provider_name == "adzuna":
            p.safety_budget.monthly = 5
        return p

    monkeypatch.setattr("app.services.quota_policy.get_provider_policy", mock_policy)

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    s = SessionLocal()
    from app.db.base import Base
    for table in reversed(Base.metadata.sorted_tables):
        s.execute(table.delete())
    s.commit()
    s.close()

    def worker():
        session = SessionLocal()
        try:
            return acquire_provider_request_slot(session, "adzuna")
        finally:
            session.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(lambda _: worker(), range(10)))

    successes = sum(1 for r in results if r)
    assert successes == 5

    s = SessionLocal()
    try:
        from app.db.models import provider_usage
        stmt = select(provider_usage.ProviderUsageModel.request_count).where(provider_usage.ProviderUsageModel.provider_name == "adzuna")
        count = s.execute(stmt).scalar()
        assert count == 5
    finally:
        s.close()


def test_concurrent_weekly_quota_different_dates(engine, monkeypatch, caplog):
    """
    Adversarial concurrency test: Competing transactions targeting different usage_date
    rows in the same ISO week must not oversubscribe the aggregate weekly limit.
    """
    caplog.set_level(logging.ERROR)
    from datetime import datetime, timezone
    from sqlalchemy import text, func
    from app.services import quota_policy
    original_get_policy = quota_policy.get_provider_policy

    def mock_policy(provider_name):
        p = original_get_policy(provider_name)
        if provider_name == "adzuna":
            p.safety_budget.weekly = 5
        return p

    monkeypatch.setattr("app.services.quota_policy.get_provider_policy", mock_policy)

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    s = SessionLocal()
    from app.db.base import Base
    for table in reversed(Base.metadata.sorted_tables):
        s.execute(table.delete())
    # Pre-seed 2 requests on Monday of that week
    s.execute(
        text("INSERT INTO provider_usage (provider_name, usage_date, request_count) VALUES ('adzuna', '2026-03-16', 2)")
    )
    s.commit()
    s.close()

    # 8 competing workers targeting different dates across Tuesday to Sunday of that same week
    test_dates = [
        datetime(2026, 3, 17, 10, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 18, 11, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 20, 13, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 21, 14, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 22, 15, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 17, 16, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 18, 17, 0, tzinfo=timezone.utc),
    ]

    def worker(dt):
        session = SessionLocal()
        try:
            return acquire_provider_request_slot(session, "adzuna", reference_time=dt)
        finally:
            session.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(worker, test_dates))

    successes = sum(1 for r in results if r)
    # Total limit is 5, 2 were pre-seeded, so exactly 3 may succeed and 5 must be rejected
    assert successes == 3

    s = SessionLocal()
    try:
        from app.db.models import provider_usage
        total = s.execute(select(func.sum(provider_usage.ProviderUsageModel.request_count)).where(
            provider_usage.ProviderUsageModel.provider_name == "adzuna"
        )).scalar()
        assert total == 5
    finally:
        s.close()


def test_concurrent_monthly_quota_different_dates(engine, monkeypatch, caplog):
    """
    Adversarial concurrency test: Competing transactions targeting different usage_date
    rows across different weeks in the same calendar month must not oversubscribe the aggregate monthly limit.
    """
    caplog.set_level(logging.ERROR)
    from datetime import datetime, timezone
    from sqlalchemy import text, func
    from app.services import quota_policy
    original_get_policy = quota_policy.get_provider_policy

    def mock_policy(provider_name):
        p = original_get_policy(provider_name)
        if provider_name == "adzuna":
            p.safety_budget.monthly = 5
        return p

    monkeypatch.setattr("app.services.quota_policy.get_provider_policy", mock_policy)

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    s = SessionLocal()
    from app.db.base import Base
    for table in reversed(Base.metadata.sorted_tables):
        s.execute(table.delete())
    # Pre-seed 2 requests in Week 1 of March
    s.execute(
        text("INSERT INTO provider_usage (provider_name, usage_date, request_count) VALUES ('adzuna', '2026-03-02', 2)")
    )
    s.commit()
    s.close()

    # 8 competing workers targeting different dates across different weeks in March
    test_dates = [
        datetime(2026, 3, 9, 10, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 16, 11, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 23, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 30, 13, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 17, 15, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 24, 16, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 31, 17, 0, tzinfo=timezone.utc),
    ]

    def worker(dt):
        session = SessionLocal()
        try:
            return acquire_provider_request_slot(session, "adzuna", reference_time=dt)
        finally:
            session.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(worker, test_dates))

    successes = sum(1 for r in results if r)
    # Total limit is 5, 2 were pre-seeded, so exactly 3 may succeed and 5 must be rejected
    assert successes == 3

    s = SessionLocal()
    try:
        from app.db.models import provider_usage
        total = s.execute(select(func.sum(provider_usage.ProviderUsageModel.request_count)).where(
            provider_usage.ProviderUsageModel.provider_name == "adzuna"
        )).scalar()
        assert total == 5
    finally:
        s.close()
