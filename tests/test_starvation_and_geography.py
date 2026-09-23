from datetime import datetime, timezone
import pytest

def test_user_demand_prioritization(db_session, monkeypatch):
    from app.services.search_selector import select_next_search
    from app.db.models.user import User
    from app.db.models.user_profile import UserProfile
    from app.db.models.user_search import UserSearch

    u = User(email="demand@test.com", password_hash="hash", is_active=True)
    db_session.add(u)
    db_session.commit()

    prof = UserProfile(user_id=u.id, preferred_roles="Python Developer", preferred_locations="Bengaluru")
    db_session.add(prof)

    us = UserSearch(user_id=u.id, query="Python Developer", location="Bengaluru", enabled=True)
    db_session.add(us)
    db_session.commit()

    res = select_next_search(db_session)
    assert res.action == "execute"
    assert res.candidate.role_canonical == "Python Developer"
    assert res.candidate.location_canonical == "Bengaluru"

def test_execution_70_provider_attribution(db_session, monkeypatch):
    """Prove Execution 70 provider attribution directly from persisted DB state."""
    from app.api.endpoints.ingestion import internal_execute_search
    from app.schemas.job_search import CanonicalSearchIntent
    from app.db.models.search_execution import SearchExecutionModel
    from app.providers.types import ProviderName
    from datetime import datetime, timezone

    # Execution 70: candidate='Junior BI Analyst', location='Bengaluru'
    claim = SearchExecutionModel(
        candidate_id=70,
        status='selected',
        selected_at=datetime.now(timezone.utc),
        query_variant="Junior BI Analyst",
        retrieval_location="Bengaluru",
        provider_name="jooble"
    )
    db_session.add(claim)
    db_session.commit()

    # Track how internal_execute_search calls run_ingestion
    ingestion_calls = []

    def fake_run_ingestion(db, provider_name, provider, query, execution_id=None):
        ingestion_calls.append({
            "provider_name": provider_name,
            "provider_class": provider.__class__.__name__,
            "query_keywords": query.keywords,
            "query_location": query.location,
            "execution_id": execution_id
        })
        from app.schemas.job_search import IngestionResult
        return IngestionResult(provider=provider_name), []

    monkeypatch.setattr("app.api.endpoints.ingestion.run_ingestion", fake_run_ingestion)

    # Setup candidate mock
    import app.services.search_selector
    monkeypatch.setattr(app.services.search_selector, "resolve_candidate", lambda db, cid: type("MockCand", (), {"role_id": "ROLE-DA-003", "location_id": "LOC-BLR-003", "priority": 1, "role_canonical": "BI Analyst", "location_canonical": "Bengaluru"})())

    # We send an intent as the worker would
    intent = CanonicalSearchIntent(
        role_id="ROLE-DA-003",
        keywords="Junior BI Analyst",
        location_id="LOC-BLR-003",
        location="Bengaluru",
        execution_id=claim.id,
        provider=ProviderName.JOOBLE
    )

    res = internal_execute_search(intent, db_session)

    # Directly prove it was attributed to Jooble Provider via the claim data!
    assert len(ingestion_calls) == 1
    call = ingestion_calls[0]
    assert call["provider_name"] == "jooble"
    assert call["provider_class"] == "JoobleProvider"
    assert call["query_keywords"] == "Junior BI Analyst"
    assert call["query_location"] == "Bengaluru"
    assert call["execution_id"] == claim.id

def test_jooble_india_configuration_semantics():
    """Prove Jooble Indian endpoint requires specific Indian key config."""
    from app.core.config import settings
    # Ensure it's not looking for generic jooble_api_key
    assert not hasattr(settings, "jooble_api_key")
    assert hasattr(settings, "jooble_in_api_key")

    from app.providers.jooble import JoobleProvider
    from app.providers.exceptions import ProviderConfigurationError

    # Check if empty fails validation
    settings.jooble_in_api_key = ""
    j = JoobleProvider()
    with pytest.raises(ProviderConfigurationError):
        j.validate_config()

def test_geographic_rejection_taxonomy_aware(db_session, monkeypatch):
    from app.services.ingestion import run_ingestion
    from app.providers.base import JobProvider
    from app.models.job import Job
    from app.schemas.job_search import JobSearchQuery

    class TestProvider(JobProvider):
        def search_jobs(self, q):
            return [
                Job(description_is_snippet=False,
                    title="Dev", company="C1", source="t", source_job_id="1", discovered_at=datetime.now(timezone.utc),
                    location="Electronic City Phase 1, Bangalore", description="1"
                ),
                Job(description_is_snippet=False,
                    title="Dev", company="C2", source="t", source_job_id="2", discovered_at=datetime.now(timezone.utc),
                    location="Las Vegas, NV", description="2"
                ),
                Job(description_is_snippet=False,
                    title="Dev", company="C3", source="t", source_job_id="3", discovered_at=datetime.now(timezone.utc),
                    location="ITPL, Bangalore", description="3"  # ITPL is related area of Whitefield (LOC-BLR-002)
                ),
                Job(description_is_snippet=False,
                    title="Dev", company="C4", source="t", source_job_id="4", discovered_at=datetime.now(timezone.utc),
                    location="Remote (Anywhere)", description="4", remote=True
                ),
                Job(description_is_snippet=False,
                    title="Dev", company="C5", source="t", source_job_id="5", discovered_at=datetime.now(timezone.utc),
                    location="Indianapolis, IN", description="5" # Should not match "India"
                ),
                Job(description_is_snippet=False,
                    title="Dev", company="C6", source="t", source_job_id="6", discovered_at=datetime.now(timezone.utc),
                    location="Hyderabad, Telangana", description="6"
                ),
                Job(description_is_snippet=False,
                    title="Dev", company="C7", source="t", source_job_id="7", discovered_at=datetime.now(timezone.utc),
                    location="Pune, Maharashtra", description="7"
                ),
                Job(description_is_snippet=False,
                    title="Dev", company="C8", source="t", source_job_id="8", discovered_at=datetime.now(timezone.utc),
                    location="Mumbai, India", description="8"
                ),
                Job(description_is_snippet=False,
                    title="Dev", company="C9", source="t", source_job_id="9", discovered_at=datetime.now(timezone.utc),
                    location="Chennai", description="9"
                ),
                Job(description_is_snippet=False,
                    title="Dev", company="C10", source="t", source_job_id="10", discovered_at=datetime.now(timezone.utc),
                    location="India", description="10" # Broad India
                ),
                Job(description_is_snippet=False,
                    title="Dev", company="C11", source="t", source_job_id="11", discovered_at=datetime.now(timezone.utc),
                    location="Karnataka, India", description="11" # Broad Karnataka
                ),
                Job(description_is_snippet=False,
                    title="Dev", company="C12", source="t", source_job_id="12", discovered_at=datetime.now(timezone.utc),
                    location="Bengaluru, Karnataka, India", description="12" # Valid full string
                )
            ]

    monkeypatch.setattr("app.services.ingestion.acquire_provider_request_slot", lambda db, n: True)
    q = JobSearchQuery(keywords="dev", location="Whitefield") # Whitefield is LOC-BLR-002
    res, _ = run_ingestion(db_session, "test", TestProvider(), q)
    assert res.invalid == 8 # Las Vegas, Indianapolis, Hyderabad, Pune, Mumbai, Chennai, India, Karnataka
    assert res.created == 4 # Electronic City, ITPL, Remote, Bengaluru Karnataka India

def test_recommendation_creation_end_to_end(db_session, monkeypatch):
    from app.api.endpoints.ingestion import internal_execute_search, claim_notifications, NotificationClaimRequest
    from app.schemas.job_search import CanonicalSearchIntent
    from app.db.models.user import User
    from app.db.models.user_profile import UserProfile
    from app.db.models.user_search import UserSearch
    from app.db.models.search_execution import SearchExecutionModel
    from app.providers.base import JobProvider
    from app.providers.types import ProviderName
    from app.models.job import Job
    from datetime import datetime, timezone

    u = User(email="e2e2@test.com", password_hash="hash", is_active=True)
    db_session.add(u)
    db_session.commit()

    prof = UserProfile(user_id=u.id, preferred_roles="Data Analyst", preferred_locations="Bengaluru")
    db_session.add(prof)

    us = UserSearch(user_id=u.id, query="Data Analyst", location="Bengaluru", enabled=True)
    db_session.add(us)
    db_session.commit()

    claim = SearchExecutionModel(
        candidate_id=1,
        status='selected',
        selected_at=datetime.now(timezone.utc),
        query_variant="Data Analyst",
        retrieval_location="Bengaluru",
        provider_name="adzuna"
    )
    db_session.add(claim)
    db_session.commit()

    class E2EProvider(JobProvider):
        def search_jobs(self, q):
            return [
                Job(description_is_snippet=False,
                    title="Junior Data Analyst", company="C", source="e2e", source_job_id="999",
                    discovered_at=datetime.now(timezone.utc), location="Bengaluru, India",
                    description="Fresher data analyst."
                )
            ]

    monkeypatch.setattr("app.api.endpoints.ingestion.create_provider", lambda n: E2EProvider())
    import app.services.ingestion
    monkeypatch.setattr("app.services.ingestion.acquire_provider_request_slot", lambda db, n: True)
    import app.services.search_selector
    monkeypatch.setattr(app.services.search_selector, "resolve_candidate", lambda db, cid: type("MockCand", (), {"role_id": "ROLE-DA-001", "location_id": "LOC-BLR-001", "priority": 1, "role_canonical": "Data Analyst", "location_canonical": "Bengaluru"})())

    intent = CanonicalSearchIntent(
        role_id="ROLE-DA-001",
        keywords="Data Analyst",
        location_id="LOC-BLR-001",
        location="Bengaluru",
        execution_id=claim.id,
        provider=ProviderName.ADZUNA
    )

    res = internal_execute_search(intent, db_session)
    from app.db.models.recommendation_history import RecommendationHistoryModel
    db_session.commit()
    recs = db_session.query(RecommendationHistoryModel).filter(RecommendationHistoryModel.user_id == u.id).all()
    assert len(recs) == 1

    # Prove Notification Claiming works
    claim_req = NotificationClaimRequest(user_id=u.id, delivery_id="test_delivery_123", limit=5)
    claim_res = claim_notifications(claim_req, db_session)
    assert len(claim_res.recommendations) == 1
    assert claim_res.recommendations[0].title == "Junior Data Analyst"
