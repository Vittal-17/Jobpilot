import pytest
from sqlalchemy import text
from app.schemas.job_search import CanonicalSearchIntent
from app.db.models.user_search import UserSearch
from app.db.models.user import User
from app.core.security import get_password_hash
from app.services.search_selector import generate_candidates, select_next_search

def test_user_search_candidate_generation(db_session):
    # Setup user
    u = User(email="a54@example.com", password_hash=get_password_hash("pass"))
    db_session.add(u)
    db_session.commit()

    # Create an enabled search
    us1 = UserSearch(user_id=u.id, query="Python", location="Remote", enabled=True)
    # Create a disabled search
    us2 = UserSearch(user_id=u.id, query="Java", enabled=False)
    db_session.add_all([us1, us2])
    db_session.commit()

    candidates = generate_candidates(db_session)

    # Assert enabled search is NOT present
    us1_candidate = next((c for c in candidates if c.candidate_id == f"user_search::{us1.id}"), None)
    assert us1_candidate is None

def test_select_next_search_picks_user_search(db_session):
    # Setup user
    u = User(email="a54_2@example.com", password_hash=get_password_hash("pass"))
    db_session.add(u)
    db_session.commit()

    # Create an enabled search
    us = UserSearch(user_id=u.id, query="RareSkill123", enabled=True)
    db_session.add(us)
    db_session.commit()

    found = False
    for _ in range(100):
        result = select_next_search(db_session, cycle_id=None)
        if result.action == 'execute' and result.candidate.candidate_id == f"user_search::{us.id}":
            found = True
            break
        if result.action == 'stop':
            break

    assert found is False, "UserSearch should NOT be selected by the execution engine"

def test_user_search_candidate_generation_is_bounded(db_session):
    u = User(email="bounded@example.com", password_hash=get_password_hash("pass"))
    db_session.add(u)
    db_session.commit()

    # We create 505 searches.
    searches = [UserSearch(user_id=u.id, query=f"Q{i}", location="L", enabled=True) for i in range(505)]
    db_session.add_all(searches)
    db_session.commit()

    candidates = generate_candidates(db_session)
    user_candidates = [c for c in candidates if c.candidate_id.startswith("user_search::")]

    # Should be capped at 500 + whatever was left in db from other tests
    # We will just assert that it's bounded (e.g. <= 500 from this user)
    # Actually wait, other tests create user searches. Let's just check the total count of user searches is <= 500.
    assert len(user_candidates) <= 500

def test_stale_claim_closes_properly(db_session):
    from app.db.models.search_execution import SearchExecutionModel

    u = User(email="stale@example.com", password_hash=get_password_hash("pass"))
    db_session.add(u)
    db_session.commit()

    us = UserSearch(user_id=u.id, query="StaleQuery", location="StaleLoc", enabled=True)
    db_session.add(us)
    db_session.commit()

    # The actual selected candidate could be anything. We specifically want to select this US.
    # We can just manually insert a selected claim for the user_search.
    claim = SearchExecutionModel(
        candidate_id=f"user_search::{us.id}",
        status='selected',
        selected_at=db_session.scalar(text("SELECT CURRENT_TIMESTAMP")),
        provider_name="adzuna"
    )
    db_session.add(claim)
    db_session.commit()

    # Delete the user search
    db_session.delete(us)
    db_session.commit()

    # Attempt to execute it
    payload = CanonicalSearchIntent(
        role_id="user_search",
        keywords="StaleQuery",
        location_id="user_search",
        location="StaleLoc",
        priority=3,
        execution_id=claim.id,
        provider="adzuna"
    )

    from fastapi import HTTPException
    from app.api.endpoints.ingestion import internal_execute_search
    try:
        internal_execute_search(payload, db_session)
        assert False, "Should have raised 409"
    except HTTPException as e:
        assert e.status_code == 409
        assert e.detail == "Execution candidate is no longer valid"

    db_session.refresh(claim)
    assert claim.status == 'failed'
    assert claim.error_message == 'stale candidate'
    assert claim.completed_at is not None

def test_execute_claimed_search_outside_bounded_window(db_session, monkeypatch):
    """Proves that a valid claimed UserSearch can be executed even if it falls out of the 500-row generation window."""
    from app.db.models.search_execution import SearchExecutionModel
    from app.schemas.job_search import CanonicalSearchIntent
    from app.api.endpoints.ingestion import internal_execute_search

    u = User(email="outside_window@example.com", password_hash=get_password_hash("pass"))
    db_session.add(u)
    db_session.commit()

    us = UserSearch(user_id=u.id, query="HiddenQuery", location="HiddenLoc", enabled=True)
    db_session.add(us)
    db_session.commit()

    # Manually create a claim
    claim = SearchExecutionModel(
        candidate_id=f"user_search::{us.id}",
        status='selected',
        selected_at=db_session.scalar(text("SELECT CURRENT_TIMESTAMP")),
        provider_name="adzuna"
    )
    db_session.add(claim)
    db_session.commit()

    # Force generate_candidates to return NOTHING, simulating the search falling out of the 500-row window
    monkeypatch.setattr("app.services.search_selector.generate_candidates", lambda db=None: [])

    # We also mock run_ingestion so it doesn't actually hit the network during execution
    from app.services.ingestion import IngestionResult
    from app.providers.types import ProviderName
    def mock_run_ingestion(db, provider_name, provider_client, query, execution_id):
        # We need to simulate a successful execution update
        claim = db.query(SearchExecutionModel).filter_by(id=execution_id).first()
        claim.status = "succeeded"
        claim.completed_at = db.scalar(text("SELECT CURRENT_TIMESTAMP"))
        db.commit()
        return IngestionResult(provider=ProviderName.ADZUNA, fetched=10, created=5, duplicates=5, invalid=0, failed=0), []

    monkeypatch.setattr("app.api.endpoints.ingestion.run_ingestion", mock_run_ingestion)

    payload = CanonicalSearchIntent(
        role_id="user_search",
        keywords="HiddenQuery",
        location_id="user_search",
        location="HiddenLoc",
        priority=3,
        execution_id=claim.id,
        provider="adzuna"
    )

    # If it relied on generate_candidates, it would fail with 409 stale candidate.
    # Because we implemented resolve_candidate, it will succeed!
    internal_execute_search(payload, db_session)

    db_session.refresh(claim)
    assert claim.status == 'succeeded', "Candidate outside window was not executed"

def test_malformed_user_search_candidate_ids(db_session):
    """Proves that resolve_candidate properly rejects malformed user_search IDs."""
    from app.services.search_selector import resolve_candidate

    malformed_ids = [
        "user_search::",
        "user_search::abc",
        "user_search::123::extra",
        "user_search::::123",
        "user_search::123a"
    ]
    for bad_id in malformed_ids:
        assert resolve_candidate(db_session, bad_id) is None

def test_taxonomy_search_generates_recommendations(db_session, monkeypatch):
    """Proves that an autonomous taxonomy search matches against active users and populates their recommendation history."""
    from app.db.models.search_execution import SearchExecutionModel
    from app.schemas.job_search import CanonicalSearchIntent
    from app.api.endpoints.ingestion import internal_execute_search
    from app.db.models.user_profile import UserProfile
    from app.db.models.recommendation_history import RecommendationHistoryModel

    # Setup a user with an enabled search (to mark them active) and a profile
    u = User(email="taxonomy_test@example.com", password_hash="pass")
    db_session.add(u)
    db_session.commit()

    prof = UserProfile(user_id=u.id, preferred_roles="Autonomy Engineer", skills="Python")
    us = UserSearch(user_id=u.id, query="Autonomy", location="London", enabled=True)
    db_session.add_all([prof, us])
    db_session.commit()

    # Manually create a claim for a taxonomy search
    claim = SearchExecutionModel(
        candidate_id="ROLE-PY-001::LOC-BLR-001",
        status='selected',
        selected_at=db_session.scalar(text("SELECT CURRENT_TIMESTAMP")),
        provider_name="adzuna"
    )
    db_session.add(claim)
    db_session.commit()

    # Mock run_ingestion to return a successfully saved job
    from app.services.ingestion import IngestionResult
    from app.providers.types import ProviderName
    from app.db.models.job import JobModel
    from datetime import datetime, timezone

    def mock_run_ingestion(db, provider_name, provider_client, query, execution_id):
        claim = db.query(SearchExecutionModel).filter_by(id=execution_id).first()
        claim.status = "succeeded"
        claim.completed_at = db.scalar(text("SELECT CURRENT_TIMESTAMP"))

        # Create a job that perfectly matches the user profile
        job = JobModel(
            title="Senior Python Developer", company="DeepMind", source="adzuna",
            source_job_id="tax_job_1", canonical_hash="hash_tax_1",
            discovered_at=datetime.now(timezone.utc)
        )
        db.add(job)
        db.flush()
        db.commit()

        return IngestionResult(provider=ProviderName.ADZUNA, fetched=1, created=1, duplicates=0, invalid=0, failed=0), [job.id]

    monkeypatch.setattr("app.api.endpoints.ingestion.run_ingestion", mock_run_ingestion)

    payload = CanonicalSearchIntent(
        role_id="ROLE-PY-001",
        keywords="Python Developer",
        location_id="LOC-BLR-001",
        location="Bengaluru",
        priority=1,
        execution_id=claim.id,
        provider="adzuna"
    )

    internal_execute_search(payload, db_session)

    # Assert that the user received the recommendation despite this NOT being a user_search execution
    recs = db_session.query(RecommendationHistoryModel).filter(RecommendationHistoryModel.user_id == u.id).all()
    assert len(recs) == 1, "Taxonomy search failed to generate recommendations for active users"
