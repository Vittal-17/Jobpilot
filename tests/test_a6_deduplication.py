import pytest
import threading
import time
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.db.repository.job_repository import save_job, normalize_url
from app.models.job import Job as PydanticJob
from app.db.models.job import JobModel
from app.db.models.job_source import JobSourceModel
from app.db.models.recommendation_history import RecommendationHistoryModel
from sqlalchemy.dialects.postgresql import insert


def test_same_provider_deduplication(db_session: Session):
    job1 = PydanticJob(title="Software Eng", company="Tech Corp", source="adzuna", source_job_id="1", discovered_at=datetime.now(timezone.utc))
    job2 = PydanticJob(title="Software Eng", company="Tech Corp", source="adzuna", source_job_id="1", discovered_at=datetime.now(timezone.utc))

    db_job1, created1 = save_job(db_session, job1)
    assert created1 is True

    db_job2, created2 = save_job(db_session, job2)
    assert created2 is False
    assert db_job1.id == db_job2.id

    sources = db_session.query(JobSourceModel).filter_by(job_id=db_job1.id).all()
    assert len(sources) == 1


def test_cross_provider_deduplication(db_session: Session):
    now = datetime.now(timezone.utc)
    job_adzuna = PydanticJob(title="Data Scientist", company="Global AI", location="Remote", source="adzuna", source_job_id="a1", discovered_at=now)
    job_jooble = PydanticJob(title="Data Scientist", company="Global AI", location="Remote", source="jooble", source_job_id="j1", discovered_at=now)

    db_job1, created1 = save_job(db_session, job_adzuna)
    assert created1 is True

    db_job2, created2 = save_job(db_session, job_jooble)
    assert created2 is False
    assert db_job1.id == db_job2.id

    sources = db_session.query(JobSourceModel).filter_by(job_id=db_job1.id).all()
    assert len(sources) == 2
    source_names = [s.source for s in sources]
    assert "adzuna" in source_names
    assert "jooble" in source_names


def test_provider_id_precedence(db_session: Session):
    now = datetime.now(timezone.utc)
    job1 = PydanticJob(title="Backend Developer", company="Corp A", location="New York", source="adzuna", source_job_id="prov_100", discovered_at=now)
    db_job1, created1 = save_job(db_session, job1)
    assert created1 is True

    job1_updated = PydanticJob(title="Senior Developer", company="Corp B", location="San Francisco", source="adzuna", source_job_id="prov_100", discovered_at=now)
    db_job2, created2 = save_job(db_session, job1_updated)

    assert created2 is False
    assert db_job1.id == db_job2.id



def test_alias_provider_id_precedence(db_session: Session):
    now = datetime.now(timezone.utc)

    # Create an Adzuna job
    job1 = PydanticJob(title="Data Scientist", company="Corp", location="NYC", source="adzuna", source_job_id="ad_100", discovered_at=now, url="https://example.com/job/100")
    db_job1, created1 = save_job(db_session, job1)
    assert created1 is True

    # Cross-provider deduplicate a Jooble alias onto it (matching canonical hash)
    job2 = PydanticJob(title="Data Scientist", company="Corp", location="NYC", source="jooble", source_job_id="jb_100", discovered_at=now, url="https://example.com/job/100")
    db_job2, created2 = save_job(db_session, job2)
    assert created2 is False
    assert db_job2.id == db_job1.id

    # Submit the same Jooble source_job_id with changed metadata
    job3 = PydanticJob(title="Senior Data Scientist", company="New Corp", location="Remote", source="jooble", source_job_id="jb_100", discovered_at=now, url="https://example.com/job/200")
    db_job3, created3 = save_job(db_session, job3)

    # Should resolve to the existing job via JobSourceModel, no new job created
    assert created3 is False
    assert db_job3.id == db_job1.id

    # Ensure only one JobSourceModel exists for this provider identity
    sources = db_session.query(JobSourceModel).filter_by(source="jooble", source_job_id="jb_100").all()
    assert len(sources) == 1

def test_incomplete_identity_prevention(db_session: Session):
    now = datetime.now(timezone.utc)
    job1 = PydanticJob(title="A", company="B", source="adzuna", source_job_id="1", discovered_at=now)
    job2 = PydanticJob(title="A", company="B", source="jooble", source_job_id="2", discovered_at=now)

    db_job1, c1 = save_job(db_session, job1)
    db_job2, c2 = save_job(db_session, job2)

    assert c1 is True
    assert c2 is True
    assert db_job1.id != db_job2.id


def test_false_positive_prevention(db_session: Session):
    now = datetime.now(timezone.utc)
    job1 = PydanticJob(title="Frontend Eng", company="Tech Corp", source="adzuna", source_job_id="1", discovered_at=now)
    job2 = PydanticJob(title="Backend Eng", company="Tech Corp", source="jooble", source_job_id="2", discovered_at=now)

    db_job1, c1 = save_job(db_session, job1)
    db_job2, c2 = save_job(db_session, job2)
    assert c2 is True
    assert db_job1.id != db_job2.id


def test_same_canonical_url_deduplication(db_session: Session):
    now = datetime.now(timezone.utc)
    job1 = PydanticJob(title="T", company="C", source="adzuna", source_job_id="1", discovered_at=now, url="https://company.com/job/123?utm_source=adzuna")
    job2 = PydanticJob(title="T2", company="C2", source="jooble", source_job_id="2", discovered_at=now, url="https://COMPANY.com/job/123?utm_campaign=xyz")

    db_job1, c1 = save_job(db_session, job1)
    db_job2, c2 = save_job(db_session, job2)

    assert c1 is True
    assert c2 is False
    assert db_job1.id == db_job2.id


def test_url_normalization_query_ordering(db_session: Session):
    now = datetime.now(timezone.utc)
    url1 = "https://example.com/job/456?b=2&a=1&utm_medium=email"
    url2 = "https://example.com/job/456?a=1&b=2&gclid=xyz123"

    assert normalize_url(url1) == normalize_url(url2)

    job1 = PydanticJob(title="X", company="Y", source="adzuna", source_job_id="u1", discovered_at=now, url=url1)
    job2 = PydanticJob(title="X", company="Y", source="jooble", source_job_id="u2", discovered_at=now, url=url2)

    db_job1, c1 = save_job(db_session, job1)
    db_job2, c2 = save_job(db_session, job2)

    assert c1 is True
    assert c2 is False
    assert db_job1.id == db_job2.id



def test_url_normalization_ref_preservation():
    # URL 1 and URL 2 differ only in 'ref', they should NOT normalize to the same string
    url1 = "https://example.com/job/789?ref=A&utm_source=test"
    url2 = "https://example.com/job/789?ref=B&utm_campaign=test"

    norm1 = normalize_url(url1)
    norm2 = normalize_url(url2)

    assert norm1 != norm2
    assert "ref=A" in norm1
    assert "ref=B" in norm2

    # URL 3 and URL 4 differ only by tracking parameters, they SHOULD normalize identically
    url3 = "https://example.com/job/789?ref=A&utm_source=adzuna"
    url4 = "https://example.com/job/789?ref=A&gclid=123"

    assert normalize_url(url3) == normalize_url(url4)

def test_concurrent_canonical_collision(db_session: Session):
    from sqlalchemy.orm import sessionmaker
    engine = db_session.get_bind()
    SessionLocal = sessionmaker(bind=engine)

    s1 = SessionLocal()
    s2 = SessionLocal()

    res1 = {}
    res2 = {}
    barrier = threading.Barrier(2)

    def worker1():
        try:
            now = datetime.now(timezone.utc)
            url = "https://concurrent-race-test.com/job/999"
            job_a = PydanticJob(title="Race Eng", company="Race Comp", source="adzuna", source_job_id="race_a", discovered_at=now, url=url)

            # Transaction 1: save_job flushes uncommitted row to DB
            db_j1, c1 = save_job(s1, job_a)
            res1["job"] = db_j1
            res1["created"] = c1

            # Wait for thread 2 to also call save_job(s2, job_b) before committing s1
            barrier.wait(timeout=5)
            time.sleep(0.2)

            # Commit transaction 1 while transaction 2 is waiting on flush lock
            s1.commit()
        except Exception as e:
            s1.rollback()
            res1["error"] = e

    def worker2():
        try:
            now = datetime.now(timezone.utc)
            url = "https://concurrent-race-test.com/job/999"
            job_b = PydanticJob(title="Race Eng", company="Race Comp", source="jooble", source_job_id="race_b", discovered_at=now, url=url)

            # Wait until thread 1 has flushed uncommitted row
            barrier.wait(timeout=5)

            # Transaction 2: attempts save_job while transaction 1 is still uncommitted
            db_j2, c2 = save_job(s2, job_b)
            s2.commit()
            res2["job"] = db_j2
            res2["created"] = c2
        except Exception as e:
            s2.rollback()
            res2["error"] = e

    t1 = threading.Thread(target=worker1)
    t2 = threading.Thread(target=worker2)

    try:
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert "error" not in res1, f"Worker 1 error: {res1.get('error')}"
        assert "error" not in res2, f"Worker 2 error: {res2.get('error')}"

        assert res1["created"] is True
        assert res2["created"] is False
        assert res1["job"].id == res2["job"].id

        sources = s1.query(JobSourceModel).filter_by(job_id=res1["job"].id).all()
        assert len(sources) == 2
        source_map = {s.source: s.source_job_id for s in sources}
        assert source_map.get("adzuna") == "race_a"
        assert source_map.get("jooble") == "race_b"
    finally:
        s1.close()
        s2.close()


def test_recommendation_history_uniqueness(db_session: Session):
    from app.db.models.user import User
    user = User(email="test@example.com", is_active=True, password_hash="test")
    db_session.add(user)
    db_session.flush()

    job = PydanticJob(title="Rec Job", company="Co", source="adzuna", source_job_id="rec1", discovered_at=datetime.now(timezone.utc))
    db_job, _ = save_job(db_session, job)

    stmt = insert(RecommendationHistoryModel).values(
        user_id=user.id,
        job_id=db_job.id
    ).on_conflict_do_nothing(
        index_elements=['user_id', 'job_id']
    )
    db_session.execute(stmt)
    db_session.commit()

    db_session.execute(stmt)
    db_session.commit()

    recs = db_session.query(RecommendationHistoryModel).filter_by(user_id=user.id).all()
    assert len(recs) == 1


def test_recommendation_deterministic_tie_breaking(db_session: Session):
    from app.db.models.user import User
    from app.db.models.user_profile import UserProfile
    from app.db.models.user_search import UserSearch
    from app.db.models.search_execution import SearchExecutionModel
    from app.api.endpoints.ingestion import internal_execute_search
    from app.schemas.job_search import CanonicalSearchIntent
    from app.providers.types import ProviderName
    from unittest.mock import patch
    from app.services.ingestion import IngestionResult

    user = User(email="ties@example.com", is_active=True, password_hash="test")
    db_session.add(user)
    db_session.flush()

    prof = UserProfile(user_id=user.id, preferred_roles="Engineer", skills="Python")
    search = UserSearch(user_id=user.id, query="Python", location="Remote", enabled=True)
    db_session.add(prof)
    db_session.add(search)
    db_session.flush()

    exec_model = SearchExecutionModel(
        candidate_id=f"user_search::{search.id}",
        provider_name="adzuna",
        status="selected",
        selected_at=datetime.now(timezone.utc)
    )
    db_session.add(exec_model)
    db_session.commit()

    intent = CanonicalSearchIntent(
        role_id="user_search", keywords="Python", location_id="user_search", location="Remote",
        priority=3, execution_id=exec_model.id, provider=ProviderName.ADZUNA
    )

    jobs = []
    for i in range(10):
        j = JobModel(description_is_snippet=False,
            title=f"Python Engineer Fresher {i}", company=f"Tie Company {i}", source="adzuna",
            source_job_id=f"tie_{i}", canonical_hash=f"tie_hash_{i}", match_score=0,
            discovered_at=datetime.now(timezone.utc)
        )
        db_session.add(j)
        db_session.flush()
        jobs.append(j.id)
    db_session.commit()

    with patch("app.api.endpoints.ingestion.run_ingestion") as mock_run:
        mock_run.return_value = (IngestionResult(provider="adzuna"), jobs)
        internal_execute_search(intent, db_session)

    recs = db_session.query(RecommendationHistoryModel).filter_by(user_id=user.id).order_by(RecommendationHistoryModel.job_id.asc()).all()
    assert len(recs) == 5
    selected_job_ids = [r.job_id for r in recs]
    assert selected_job_ids == sorted(jobs[:5])


def test_recommendation_top_5_batch_selection_and_exclusion(db_session: Session):
    from app.db.models.user import User
    from app.db.models.user_profile import UserProfile
    from app.db.models.user_search import UserSearch
    from app.db.models.search_execution import SearchExecutionModel
    from app.api.endpoints.ingestion import internal_execute_search
    from app.schemas.job_search import CanonicalSearchIntent
    from app.providers.types import ProviderName
    from unittest.mock import patch
    from app.services.ingestion import IngestionResult

    user = User(email="batches@example.com", is_active=True, password_hash="test")
    db_session.add(user)
    db_session.flush()

    prof = UserProfile(user_id=user.id, preferred_roles="Engineer", skills="Python", experience_years=5)
    search = UserSearch(user_id=user.id, query="Python", location="Remote", enabled=True)
    db_session.add(prof)
    db_session.add(search)
    db_session.flush()

    exec1 = SearchExecutionModel(
        candidate_id=f"user_search::{search.id}",
        provider_name="adzuna",
        status="selected",
        selected_at=datetime.now(timezone.utc)
    )
    db_session.add(exec1)
    db_session.commit()

    intent1 = CanonicalSearchIntent(
        role_id="user_search", keywords="Python", location_id="user_search", location="Remote",
        priority=3, execution_id=exec1.id, provider=ProviderName.ADZUNA
    )

    batch1_job_ids = []
    for i in range(10):
        j = JobModel(description_is_snippet=False,
            title=f"Python Engineer Fresher {i}", company=f"Company {i}", source="adzuna",
            source_job_id=f"b1_{i}", canonical_hash=f"b1_hash_{i}", match_score=0,
            discovered_at=datetime.now(timezone.utc)
        )
        db_session.add(j)
        db_session.flush()
        batch1_job_ids.append(j.id)
    db_session.commit()

    with patch("app.api.endpoints.ingestion.run_ingestion") as mock_run:
        mock_run.return_value = (IngestionResult(provider="adzuna"), batch1_job_ids)
        internal_execute_search(intent1, db_session)

    recs1 = db_session.query(RecommendationHistoryModel).filter_by(user_id=user.id).order_by(RecommendationHistoryModel.job_id.asc()).all()
    assert len(recs1) == 5
    batch1_recommended_job_ids = {r.job_id for r in recs1}
    assert batch1_recommended_job_ids == set(batch1_job_ids[:5])

    exec1.started_at = datetime.now(timezone.utc)
    exec1.completed_at = datetime.now(timezone.utc)
    exec1.jobs_fetched = 10
    exec1.jobs_created = 10
    exec1.jobs_duplicates = 0
    exec1.jobs_invalid = 0
    exec1.status = "succeeded"
    db_session.commit()

    batch2_job_ids = []
    for i in range(10):
        j = JobModel(description_is_snippet=False,
            title=f"Python Engineer Fresher Fresher Fresh {i}", company=f"Company Fresh {i}", source="adzuna",
            source_job_id=f"b2_{i}", canonical_hash=f"b2_hash_{i}", match_score=0,
            discovered_at=datetime.now(timezone.utc)
        )
        db_session.add(j)
        db_session.flush()
        batch2_job_ids.append(j.id)
    db_session.commit()

    exec2 = SearchExecutionModel(
        candidate_id=f"user_search::{search.id}",
        provider_name="adzuna",
        status="selected",
        selected_at=datetime.now(timezone.utc)
    )
    db_session.add(exec2)
    db_session.commit()

    intent2 = CanonicalSearchIntent(
        role_id="user_search", keywords="Python", location_id="user_search", location="Remote",
        priority=3, execution_id=exec2.id, provider=ProviderName.ADZUNA
    )

    combined_pool_input = list(batch1_recommended_job_ids) + batch2_job_ids

    with patch("app.api.endpoints.ingestion.run_ingestion") as mock_run:
        mock_run.return_value = (IngestionResult(provider="adzuna"), combined_pool_input)
        internal_execute_search(intent2, db_session)

    recs2 = db_session.query(RecommendationHistoryModel).filter_by(user_id=user.id).order_by(RecommendationHistoryModel.job_id.asc()).all()
    assert len(recs2) == 10

    all_rec_job_ids = {r.job_id for r in recs2}
    newly_recommended_job_ids = all_rec_job_ids - batch1_recommended_job_ids

    assert len(newly_recommended_job_ids) == 5
    assert newly_recommended_job_ids == set(batch2_job_ids[:5])
    assert batch1_recommended_job_ids.isdisjoint(newly_recommended_job_ids)


def test_recommendation_failure_propagation(db_session: Session):
    from app.db.models.user import User
    from app.db.models.user_profile import UserProfile
    from app.db.models.user_search import UserSearch
    from app.db.models.search_execution import SearchExecutionModel
    from app.api.endpoints.ingestion import internal_execute_search
    from app.schemas.job_search import CanonicalSearchIntent
    from app.providers.types import ProviderName
    from fastapi import HTTPException
    from unittest.mock import patch
    from app.services.ingestion import IngestionResult

    user = User(email="fail@example.com", is_active=True, password_hash="test")
    db_session.add(user)
    db_session.flush()

    search = UserSearch(user_id=user.id, query="Python", location="Remote", enabled=True)
    db_session.add(search)
    db_session.flush()

    exec_model = SearchExecutionModel(
        candidate_id=f"user_search::{search.id}",
        provider_name="adzuna",
        status="selected",
        selected_at=datetime.now(timezone.utc)
    )
    db_session.add(exec_model)
    db_session.commit()

    intent = CanonicalSearchIntent(
        role_id="user_search", keywords="Python", location_id="user_search", location="Remote",
        priority=3, execution_id=exec_model.id, provider=ProviderName.ADZUNA
    )

    j = JobModel(description_is_snippet=False,
        title="Python Engineer Fresher Fail", company="Fail Corp", source="adzuna",
        source_job_id="fail_1", canonical_hash="fail_hash_1", match_score=0,
        discovered_at=datetime.now(timezone.utc)
    )
    db_session.add(j)
    db_session.commit()

    with patch("app.api.endpoints.ingestion.run_ingestion") as mock_run, \
         patch("app.services.matching_service.calculate_match", side_effect=Exception("Match engine error")):
        mock_run.return_value = (IngestionResult(provider="adzuna"), [j.id])
        with pytest.raises(HTTPException) as exc_info:
            internal_execute_search(intent, db_session)
        assert exc_info.value.status_code == 500
        assert "Failed to process recommendations" in exc_info.value.detail
