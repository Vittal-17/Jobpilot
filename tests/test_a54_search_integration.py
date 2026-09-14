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

    # Assert enabled search is present
    us1_candidate = next((c for c in candidates if c.candidate_id == f"user_search::{us1.id}"), None)
    assert us1_candidate is not None
    assert us1_candidate.role_canonical == "Python"
    assert us1_candidate.location_canonical == "Remote"
    assert us1_candidate.role_id == "user_search"

    # Assert disabled search is excluded
    us2_candidate = next((c for c in candidates if c.candidate_id == f"user_search::{us2.id}"), None)
    assert us2_candidate is None

    # Test deleted search is excluded
    db_session.delete(us1)
    db_session.commit()
    candidates_after_delete = generate_candidates(db_session)
    us1_candidate_after = next((c for c in candidates_after_delete if c.candidate_id == f"user_search::{us1.id}"), None)
    assert us1_candidate_after is None

def test_select_next_search_picks_user_search(db_session):
    # Setup user
    u = User(email="a54_2@example.com", password_hash=get_password_hash("pass"))
    db_session.add(u)
    db_session.commit()

    # Create an enabled search
    us = UserSearch(user_id=u.id, query="RareSkill123", enabled=True)
    db_session.add(us)
    db_session.commit()

    # Call select_next_search
    # It should pick a candidate. Since we don't know exact priorities of taxonomy vs user search,
    # we can just keep calling select_next_search until it picks our user search or stops.
    # Note: cycle_id is None for unbudgeted loop.
    found = False
    for _ in range(1000):
        result = select_next_search(db_session, cycle_id=None)
        if result.action == 'execute' and result.candidate.candidate_id == f"user_search::{us.id}":
            found = True
            break
        if result.action == 'stop':
            break

    assert found is True, "UserSearch was not selected by the execution engine"

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
        return IngestionResult(provider=ProviderName.ADZUNA, fetched=10, created=5, duplicates=5, invalid=0, failed=0)

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
