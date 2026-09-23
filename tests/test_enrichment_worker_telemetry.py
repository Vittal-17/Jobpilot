import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from sqlalchemy import text
from app.db.models.job import JobModel
from app.db.models.job_enrichment import JobEnrichmentModel
from app.db.models.search_execution import SearchExecutionModel
from app.db.models.user import User
from app.db.models.user_profile import UserProfile
from app.db.models.user_search import UserSearch
from app.db.models.recommendation_history import RecommendationHistoryModel
from app.services.enrichment_worker import EnrichmentWorker

def _cleanup(db_session):
    db_session.rollback()
    db_session.execute(text("DELETE FROM recommendation_history"))
    db_session.execute(text("DELETE FROM job_enrichments"))
    db_session.execute(text("DELETE FROM jobs"))
    db_session.execute(text("DELETE FROM search_execution"))
    db_session.execute(text("DELETE FROM user_searches"))
    db_session.execute(text("DELETE FROM user_profiles"))
    db_session.execute(text("DELETE FROM users"))
    db_session.commit()

# Ensure we use a valid fresher-eligible description that passes length check
VALID_DESC = "<html><body>Data Engineer 0-1 years python sql. This is a very long description that goes on and on so that the extractor does not think it is a CAPTCHA or SPA snippet. Here are a lot more words to easily pass the length requirement.</body></html>"

def test_async_enrichment_telemetry_sync_before_async(db_session, monkeypatch):
    _cleanup(db_session)
    try:
        now = datetime.now(timezone.utc)
        exec_id = 2001
        db_session.add(SearchExecutionModel(id=exec_id, candidate_id="ROLE::LOC", status="succeeded", selected_at=now, jobs_fresher_eligible=1, recommendations_created=1))
        db_session.flush()

        db_session.add(User(id=2001, email="test2001@test.com", password_hash="hash", is_active=True))
        db_session.flush()
        db_session.add(UserProfile(user_id=2001, preferred_roles="Data", experience_years=0))
        db_session.add(UserSearch(id=2001, user_id=2001, query="Data", location="Remote", enabled=True))

        db_session.add(JobModel(id=2001, title="Data Engineer", company="C", source="test", source_job_id="T2001", discovered_at=now, description_is_snippet=True, url="http://test"))
        db_session.flush()
        db_session.add(JobEnrichmentModel(job_id=2001, status="pending", url="http://test", source_execution_id=exec_id))
        db_session.commit()

        worker = EnrichmentWorker()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.text = VALID_DESC
        worker.ssrf_client.fetch = MagicMock(return_value=mock_response)

        worker.claim_and_process(db_session)

        ex = db_session.query(SearchExecutionModel).filter_by(id=exec_id).first()
        db_session.refresh(ex)
        assert ex.jobs_fresher_eligible == 2
        assert ex.recommendations_created == 2
    finally:
        _cleanup(db_session)

def test_async_enrichment_telemetry_async_before_sync(db_session, monkeypatch):
    _cleanup(db_session)
    try:
        now = datetime.now(timezone.utc)
        exec_id = 2002
        db_session.add(SearchExecutionModel(id=exec_id, candidate_id="ROLE::LOC", status="succeeded", selected_at=now, jobs_fresher_eligible=0, recommendations_created=0))
        db_session.flush()

        db_session.add(User(id=2002, email="test2002@test.com", password_hash="hash", is_active=True))
        db_session.flush()
        db_session.add(UserProfile(user_id=2002, preferred_roles="Data", experience_years=0))
        db_session.add(UserSearch(id=2002, user_id=2002, query="Data", location="Remote", enabled=True))

        db_session.add(JobModel(id=2002, title="Data Engineer", company="C", source="test", source_job_id="T2002", discovered_at=now, description_is_snippet=True, url="http://test"))
        db_session.flush()
        db_session.add(JobEnrichmentModel(job_id=2002, status="pending", url="http://test", source_execution_id=exec_id))
        db_session.commit()

        worker = EnrichmentWorker()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.text = VALID_DESC
        worker.ssrf_client.fetch = MagicMock(return_value=mock_response)

        worker.claim_and_process(db_session)

        db_session.execute(text("""
            UPDATE search_execution
            SET jobs_fresher_eligible = COALESCE(jobs_fresher_eligible, 0) + 1,
                recommendations_created = COALESCE(recommendations_created, 0) + 1
            WHERE id = :eid
        """), {"eid": exec_id})
        db_session.commit()

        ex = db_session.query(SearchExecutionModel).filter_by(id=exec_id).first()
        db_session.refresh(ex)
        assert ex.jobs_fresher_eligible == 2
        assert ex.recommendations_created == 2
    finally:
        _cleanup(db_session)

def test_async_enrichment_telemetry_multiple_successful(db_session, monkeypatch):
    _cleanup(db_session)
    try:
        now = datetime.now(timezone.utc)
        exec_id = 2003
        db_session.add(SearchExecutionModel(id=exec_id, candidate_id="ROLE::LOC", status="succeeded", selected_at=now, jobs_fresher_eligible=0, recommendations_created=0))
        db_session.flush()

        db_session.add(User(id=2003, email="test2003@test.com", password_hash="hash", is_active=True))
        db_session.flush()
        db_session.add(UserProfile(user_id=2003, preferred_roles="Data", experience_years=0))
        db_session.add(UserSearch(id=2003, user_id=2003, query="Data", location="Remote", enabled=True))

        db_session.add(JobModel(id=2003, title="Data Engineer", company="C", source="test", source_job_id="T2003", discovered_at=now, description_is_snippet=True, url="http://test"))
        db_session.add(JobModel(id=2004, title="Data Engineer", company="C", source="test", source_job_id="T2004", discovered_at=now, description_is_snippet=True, url="http://test"))
        db_session.flush()
        db_session.add(JobEnrichmentModel(job_id=2003, status="pending", url="http://test", source_execution_id=exec_id))
        db_session.add(JobEnrichmentModel(job_id=2004, status="pending", url="http://test", source_execution_id=exec_id))
        db_session.commit()

        worker = EnrichmentWorker()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.text = VALID_DESC
        worker.ssrf_client.fetch = MagicMock(return_value=mock_response)

        worker.claim_and_process(db_session)

        ex = db_session.query(SearchExecutionModel).filter_by(id=exec_id).first()
        db_session.refresh(ex)
        assert ex.jobs_fresher_eligible == 2
        assert ex.recommendations_created == 2
    finally:
        _cleanup(db_session)

def test_async_enrichment_telemetry_first_discoverer(db_session, monkeypatch):
    _cleanup(db_session)
    try:
        now = datetime.now(timezone.utc)
        exec1_id = 2005
        exec2_id = 2006
        db_session.add(SearchExecutionModel(id=exec1_id, candidate_id="ROLE::LOC", status="succeeded", selected_at=now, jobs_fresher_eligible=0, recommendations_created=0))
        db_session.add(SearchExecutionModel(id=exec2_id, candidate_id="ROLE::LOC", status="succeeded", selected_at=now, jobs_fresher_eligible=0, recommendations_created=0))
        db_session.flush()

        db_session.add(User(id=2005, email="test2005@test.com", password_hash="hash", is_active=True))
        db_session.flush()
        db_session.add(UserProfile(user_id=2005, preferred_roles="Data", experience_years=0))
        db_session.add(UserSearch(id=2005, user_id=2005, query="Data", location="Remote", enabled=True))

        db_session.add(JobModel(id=2005, title="Data Engineer", company="C", source="test", source_job_id="T2005", discovered_at=now, description_is_snippet=True, url="http://test"))
        db_session.flush()
        db_session.add(JobEnrichmentModel(job_id=2005, status="pending", url="http://test", source_execution_id=exec1_id))
        db_session.commit()

        worker = EnrichmentWorker()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.text = VALID_DESC
        worker.ssrf_client.fetch = MagicMock(return_value=mock_response)

        worker.claim_and_process(db_session)

        ex1 = db_session.query(SearchExecutionModel).filter_by(id=exec1_id).first()
        db_session.refresh(ex1)
        ex2 = db_session.query(SearchExecutionModel).filter_by(id=exec2_id).first()
        db_session.refresh(ex2)
        assert ex1.jobs_fresher_eligible == 1
        assert ex2.jobs_fresher_eligible == 0
        assert ex1.recommendations_created == 1
        assert ex2.recommendations_created == 0
    finally:
        _cleanup(db_session)

def test_async_enrichment_telemetry_retry_no_double_credit(db_session, monkeypatch):
    _cleanup(db_session)
    try:
        now = datetime.now(timezone.utc)
        exec_id = 2007
        db_session.add(SearchExecutionModel(id=exec_id, candidate_id="ROLE::LOC", status="succeeded", selected_at=now, jobs_fresher_eligible=0, recommendations_created=0))
        db_session.flush()

        db_session.add(JobModel(id=2007, title="Data Engineer", company="C", source="test", source_job_id="T2007", discovered_at=now, description_is_snippet=True, url="http://test"))
        db_session.flush()
        db_session.add(JobEnrichmentModel(job_id=2007, status="pending", url="http://test", source_execution_id=exec_id))
        db_session.commit()

        worker = EnrichmentWorker()

        # We pass VALID_DESC so it passes eligibility
        worker.complete_success(db_session, 2007, "some-token", VALID_DESC, exec_id)

        ex = db_session.query(SearchExecutionModel).filter_by(id=exec_id).first()
        db_session.refresh(ex)
        assert ex.jobs_fresher_eligible == 0 # no token match

        # claim it
        db_session.execute(text("UPDATE job_enrichments SET status = 'in_progress', lease_token = 'token', lease_expires_at = clock_timestamp() + interval '5 min' WHERE job_id = 2007"))
        db_session.commit()

        worker.complete_success(db_session, 2007, "token", VALID_DESC, exec_id)
        ex = db_session.query(SearchExecutionModel).filter_by(id=exec_id).first()
        db_session.refresh(ex)
        assert ex.jobs_fresher_eligible == 1

        # Retry with same token
        worker.complete_success(db_session, 2007, "token", VALID_DESC, exec_id)
        db_session.refresh(ex)
        assert ex.jobs_fresher_eligible == 1 # still 1
    finally:
        _cleanup(db_session)

def test_async_enrichment_telemetry_abandoned_execution(db_session, monkeypatch):
    _cleanup(db_session)
    try:
        now = datetime.now(timezone.utc)
        exec_id = 2008
        db_session.add(SearchExecutionModel(id=exec_id, candidate_id="ROLE::LOC", status="failed", selected_at=now, jobs_fresher_eligible=0, recommendations_created=0))
        db_session.flush()

        db_session.add(JobModel(id=2008, title="Data Engineer", company="C", source="test", source_job_id="T2008", discovered_at=now, description_is_snippet=True, url="http://test"))
        db_session.flush()
        db_session.add(JobEnrichmentModel(job_id=2008, status="pending", url="http://test", source_execution_id=exec_id))
        db_session.commit()

        worker = EnrichmentWorker()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.text = VALID_DESC
        worker.ssrf_client.fetch = MagicMock(return_value=mock_response)

        worker.claim_and_process(db_session)

        ex = db_session.query(SearchExecutionModel).filter_by(id=exec_id).first()
        db_session.refresh(ex)
        assert ex.jobs_fresher_eligible == 0
    finally:
        _cleanup(db_session)
