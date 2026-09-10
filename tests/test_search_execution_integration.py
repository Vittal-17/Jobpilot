from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, text

from app.db.models.search_execution import SearchExecutionModel
from app.providers.exceptions import ProviderConfigurationError
from app.schemas.job_search import JobSearchQuery
from app.services.ingestion import InvalidExecutionTransition, run_ingestion


class EmptyProvider:
    def validate_config(self):
        return None

    def search_jobs(self, query):
        return []


class MissingConfigProvider:
    def validate_config(self):
        raise ProviderConfigurationError("missing")


def _claim(db_session, candidate_id="ROLE-TEST::LOC-TEST"):
    claim = SearchExecutionModel(
        candidate_id=candidate_id,
        status="selected",
        selected_at=datetime.now(timezone.utc),
    )
    db_session.add(claim)
    db_session.commit()
    return claim


def test_migrated_search_execution_constraints_exist(engine):
    inspector = inspect(engine)
    checks = {item["name"] for item in inspector.get_check_constraints("search_execution")}
    indexes = {item["name"]: item for item in inspector.get_indexes("search_execution")}

    assert "chk_status_valid" in checks
    assert indexes["uq_active_claim"]["unique"] is True
    predicate = str(indexes["uq_active_claim"]["dialect_options"]["postgresql_where"])
    assert "selected" in predicate and "started" in predicate


def test_invalid_execution_status_is_rejected(db_session):
    with pytest.raises(Exception) as exc_info:
        db_session.execute(
            text(
                "INSERT INTO search_execution (candidate_id, status, selected_at) "
                "VALUES ('ROLE-X::LOC-X', 'invalid', CURRENT_TIMESTAMP)"
            )
        )
        db_session.commit()
    db_session.rollback()
    assert getattr(exc_info.value.orig, "sqlstate", None) == "23514"


def test_started_claim_blocks_second_active_claim(db_session):
    claim = _claim(db_session)
    claim.status = "started"
    claim.started_at = datetime.now(timezone.utc)
    db_session.commit()

    db_session.add(
        SearchExecutionModel(
            candidate_id=claim.candidate_id,
            status="selected",
            selected_at=datetime.now(timezone.utc),
        )
    )
    with pytest.raises(Exception) as exc_info:
        db_session.commit()
    db_session.rollback()
    assert getattr(exc_info.value.orig, "sqlstate", None) == "23505"


def test_ingestion_lifecycle_records_actual_metrics(db_session):
    claim = _claim(db_session)
    selected_at = claim.selected_at

    result = run_ingestion(
        db_session,
        "adzuna",
        EmptyProvider(),
        JobSearchQuery(keywords="test", location="test"),
        claim.id,
    )

    db_session.refresh(claim)
    assert result.fetched == 0
    assert claim.status == "succeeded"
    assert claim.selected_at == selected_at
    assert claim.started_at is not None
    assert claim.completed_at is not None
    assert claim.jobs_fetched == 0
    assert claim.jobs_created == 0


def test_configuration_failure_marks_claim_failed_without_quota(db_session):
    claim = _claim(db_session)

    with pytest.raises(ProviderConfigurationError):
        run_ingestion(
            db_session,
            "adzuna",
            MissingConfigProvider(),
            JobSearchQuery(keywords="test", location="test"),
            claim.id,
        )

    db_session.refresh(claim)
    assert claim.status == "failed"
    assert claim.completed_at is not None
    assert claim.error_message == "Provider configuration error"
    assert db_session.execute(text("SELECT COUNT(*) FROM provider_usage")).scalar_one() == 0
    assert db_session.execute(text("SELECT COUNT(*) FROM provider_minute_usage")).scalar_one() == 0


def test_terminal_execution_replay_is_rejected(db_session):
    claim = _claim(db_session)
    run_ingestion(
        db_session,
        "adzuna",
        EmptyProvider(),
        JobSearchQuery(keywords="test", location="test"),
        claim.id,
    )

    with pytest.raises(InvalidExecutionTransition):
        run_ingestion(
            db_session,
            "adzuna",
            EmptyProvider(),
            JobSearchQuery(keywords="test", location="test"),
            claim.id,
        )
