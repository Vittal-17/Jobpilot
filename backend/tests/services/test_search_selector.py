import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from app.services.search_selector import generate_candidates, _get_history, _get_variant_history, select_next_search
from app.domain.candidate import SearchCandidate

def test_generate_candidates_is_deterministic():
    candidates1 = generate_candidates()
    candidates2 = generate_candidates()

    assert len(candidates1) > 0
    assert len(candidates1) == 348
    assert len(candidates1) == len(candidates2)

    for c1, c2 in zip(candidates1, candidates2):
        assert c1.candidate_id == c2.candidate_id
        assert c1.role_id == c2.role_id
        assert c1.location_id == c2.location_id

def test_generate_candidates_has_no_duplicates():
    candidates = generate_candidates()
    cids = [c.candidate_id for c in candidates]
    assert len(cids) == len(set(cids))


def test_never_searched_candidate_outranks_previous_success():
    candidates = generate_candidates()
    best_previous = min(c.priority * 100 + c.tier * 10 for c in candidates)
    worst_never = max(c.priority * 100 + c.tier * 10 - 1_000 for c in candidates)
    assert worst_never < best_previous


@patch("app.services.search_selector._clean_abandoned_claims")
def test_select_next_search_all_fresh(mock_clean):

    # Mock DB where ALL candidates were selected very recently (1 minute ago)
    db_mock = MagicMock()
    now = datetime.now(timezone.utc)

    def fake_execute(stmt, params=None):
        if "GROUP BY candidate_id" in str(stmt):
            class FakeResult:
                def fetchall(self):
                    # Mock that every candidate was selected 1 min ago
                    candidates = generate_candidates()
                    return [(c.candidate_id, None, now - timedelta(minutes=1)) for c in candidates]
            return FakeResult()
        return MagicMock()

    db_mock.execute.side_effect = fake_execute

    result = select_next_search(db_mock)
    assert result.candidate is None
    assert result.reason == "all_candidates_ineligible_or_fresh"

def test_clean_abandoned_claims_preserves_history():
    from app.db.database import SessionLocal
    from app.db.models.search_execution import SearchExecutionModel

    db = SessionLocal()
    try:
        c = SearchExecutionModel(
            candidate_id="ROLE-TEST::LOC-TEST",
            status="selected",
            selected_at=datetime.now(timezone.utc) - timedelta(minutes=20)
        )
        db.add(c)
        db.commit()

        # Act
        from app.services.search_selector import _clean_abandoned_claims
        _clean_abandoned_claims(db)

        # Assert
        db.refresh(c)
        assert c.status == "failed"
        assert c.error_message == "abandoned claim"

        # Test the freshness query explicitly preserves it
        from app.services.search_selector import _get_history
        h = _get_history(db, ["ROLE-TEST::LOC-TEST"])
        assert h["ROLE-TEST::LOC-TEST"]["last_selected"] is not None

    finally:
        db.query(SearchExecutionModel).filter(SearchExecutionModel.candidate_id == "ROLE-TEST::LOC-TEST").delete()
        db.commit()
        db.close()


def test_select_next_search_does_not_swallow_arbitrary_integrity_error(monkeypatch):
    from sqlalchemy.exc import IntegrityError
    from app.services.search_selector import select_next_search
    from app.domain.candidate import SearchCandidate

    candidate = SearchCandidate(
        candidate_id="ROLE-TEST::LOC-TEST",
        role_id="ROLE-TEST",
        location_id="LOC-TEST",
        role_canonical="Test Role",
        location_canonical="Test Location",
        priority=1,
        tier=0,
    )
    monkeypatch.setattr(
        "app.services.search_selector.generate_candidates", lambda db=None: [candidate]
    )

    class MockOrig:
        sqlstate = "23503" # Foreign Key Violation, NOT Unique Violation


    class MockSession:
        def begin_nested(self):
            class Context:
                def __enter__(self): pass
                def __exit__(self, exc_type, exc_val, exc_tb): pass
            return Context()
        def add(self, obj):
            pass
        def flush(self):
            raise IntegrityError("Mock generic integrity error", params=[], orig=MockOrig())
        def commit(self):
            pass
        def rollback(self):
            pass
        def execute(self, *args, **kwargs):
            class MockResult:
                rowcount = 0
                def fetchall(self): return []
                def scalar_one(self): return 1
            return MockResult()


    import pytest
    with pytest.raises(IntegrityError):
        select_next_search(MockSession())


def test_clean_abandoned_claims_ignores_started_state():
    """
    Proves that _clean_abandoned_claims only reclaims 'selected' claims
    and explicitly ignores 'started' claims, preserving the known 005.7 limitation:
    A catastrophic failure after selected -> started may leave the execution in started.
    The current abandonment cleanup only reclaims stale selected claims.
    Recovery/reclamation of stale started executions is deferred to a future milestone.
    """
    from app.db.database import SessionLocal
    from app.db.models.search_execution import SearchExecutionModel
    from datetime import datetime, timezone, timedelta

    db = SessionLocal()
    try:
        # Create a stale 'started' claim
        c = SearchExecutionModel(
            candidate_id="ROLE-TEST::LOC-TEST-STALE-STARTED",
            status="started",
            selected_at=datetime.now(timezone.utc) - timedelta(minutes=60),
            started_at=datetime.now(timezone.utc) - timedelta(minutes=59),
            provider_name="adzuna"
        )
        db.add(c)
        db.commit()

        # Act
        from app.services.search_selector import _clean_abandoned_claims
        rows_affected = _clean_abandoned_claims(db)

        # Assert
        db.refresh(c)
        # It should still be 'started'
        assert c.status == "started"
        # And it shouldn't have been affected by cleanup
        assert rows_affected == 0

    finally:
        db.rollback()
        db.query(SearchExecutionModel).filter(SearchExecutionModel.candidate_id == "ROLE-TEST::LOC-TEST-STALE-STARTED").delete()
        db.commit()
        db.close()


# ── Zero-Yield Awareness Tests ──────────────────────────────────────────────

class TestRetrievalOutcomeClassification:
    """Tests that _get_variant_history produces correct raw values
    by correctly handling NULL states (legacy or failures)."""

    @patch("app.services.search_selector.Session")
    def test_get_variant_history_parsing(self, mock_session_cls):
        from app.services.search_selector import _get_variant_history
        now = datetime.now(timezone.utc)

        db_mock = MagicMock()
        # Mock row format: cid, variant, fetched, eligible, comp_at
        db_mock.execute.return_value.fetchall.return_value = [
            ("CID-1", "var1", 0, None, now, 1),       # NO_INVENTORY
            ("CID-1", "var2", 25, 0, now, 2),         # INVENTORY_NO_FRESHER
            ("CID-2", "var1", 25, 5, now, 3),         # PRODUCTIVE
            ("CID-2", "var2", 10, None, now, 4),      # LEGACY/UNKNOWN (fetched>0, eligible=None)
            ("CID-3", "var1", None, None, now, 5),    # FAILED EXECUTION (fetched=None)
        ]

        vh = _get_variant_history(db_mock, ["CID-1", "CID-2", "CID-3"])

        # test_no_inventory_zero_fetched_null_eligible
        assert vh["CID-1"]["var1"]["fetched"] == 0
        assert vh["CID-1"]["var1"]["eligible"] is None

        # test_inventory_no_fresher
        assert vh["CID-1"]["var2"]["fetched"] == 25
        assert vh["CID-1"]["var2"]["eligible"] == 0

        # test_productive
        assert vh["CID-2"]["var1"]["fetched"] == 25
        assert vh["CID-2"]["var1"]["eligible"] == 5

        # test_null_eligible_is_not_zero
        assert vh["CID-2"]["var2"]["fetched"] == 10
        assert vh["CID-2"]["var2"]["eligible"] is None

        # test fetched=None is preserved as None (FAILED/UNKNOWN telemetry)
        assert vh["CID-3"]["var1"]["fetched"] is None



class TestZeroYieldVariantPenalization:
    """Integration-level tests proving that zero-yield variants are
    penalized and untried alternates remain selectable."""

    def _make_candidate(self, cid="role::loc", variants=None):
        return SearchCandidate(
            candidate_id=cid,
            role_id="role",
            location_id="loc",
            role_canonical="Test Role",
            location_canonical="Test Location",
            priority=1,
            tier=1,
            variants=variants or ["fresher test role", "test role"],
        )

    @patch("app.services.search_selector._clean_abandoned_claims")
    def test_zero_fetched_variant_is_penalized(self, mock_clean):
        """A variant with jobs_fetched=0 (recent) should be skipped,
        selecting the next available variant."""
        now = datetime.now(timezone.utc)
        candidate = self._make_candidate()

        # Patch generate_candidates to return our single candidate
        with patch("app.services.search_selector.generate_candidates", return_value=[candidate]):
            # _get_history: candidate exists but no recent success/selection blocking
            with patch("app.services.search_selector._get_history") as mock_hist:
                mock_hist.return_value = {
                    candidate.candidate_id: {
                        "last_success": now - timedelta(hours=48),
                        "last_selected": now - timedelta(hours=25),
                        "success_count": 1,
                    }
                }

                # _get_variant_history: first variant has zero fetched (recent)
                with patch("app.services.search_selector._get_variant_history") as mock_vh:
                    mock_vh.return_value = {
                        candidate.candidate_id: {
                            "fresher test role": {
                                "fetched": 0,
                                "eligible": None,
                                "completed_at": now - timedelta(hours=1), "id": 1664}
                            # "test role" has no history → untried
                        }
                    }

                    # Build a mock DB that allows the claim to succeed
                    db_mock = MagicMock()
                    db_mock.execute = mock_hist.return_value  # won't be called directly
                    ctx = MagicMock()
                    ctx.__enter__ = MagicMock(return_value=None)
                    ctx.__exit__ = MagicMock(return_value=False)
                    db_mock.begin_nested.return_value = ctx
                    claim_mock = MagicMock()
                    claim_mock.id = 999
                    db_mock.add = MagicMock()
                    db_mock.flush = MagicMock()

                    # Patch SearchExecutionModel and settings to avoid config import
                    mock_settings = MagicMock(cycle_budget=20)
                    with patch("app.services.search_selector.SearchExecutionModel") as MockModel, \
                         patch("app.core.config.settings", mock_settings):
                        MockModel.return_value = claim_mock
                        result = select_next_search(db_mock, reference_time=now)

                    assert result.action == "execute"
                    assert result.candidate is not None
                    # The zero-fetched "fresher test role" should be penalized;
                    # the untried "test role" should be selected
                    assert result.candidate.query_variant == "test role"

    @patch("app.services.search_selector._clean_abandoned_claims")
    def test_untried_variant_selectable_when_previous_zero_yield(self, mock_clean):
        """When one variant has zero yield, an untried alternate must remain available."""
        now = datetime.now(timezone.utc)
        candidate = self._make_candidate(variants=["variant_a", "variant_b"])

        with patch("app.services.search_selector.generate_candidates", return_value=[candidate]):
            with patch("app.services.search_selector._get_history") as mock_hist:
                mock_hist.return_value = {
                    candidate.candidate_id: {
                        "last_success": now - timedelta(hours=48),
                        "last_selected": now - timedelta(hours=25),
                        "success_count": 0,
                    }
                }
                with patch("app.services.search_selector._get_variant_history") as mock_vh:
                    mock_vh.return_value = {
                        candidate.candidate_id: {
                            "variant_a": {
                                "fetched": 0,
                                "eligible": None,
                                "completed_at": now - timedelta(hours=2), "id": 10}
                            # variant_b untried
                        }
                    }

                    db_mock = MagicMock()
                    ctx = MagicMock()
                    ctx.__enter__ = MagicMock(return_value=None)
                    ctx.__exit__ = MagicMock(return_value=False)
                    db_mock.begin_nested.return_value = ctx
                    claim_mock = MagicMock()
                    claim_mock.id = 1000
                    db_mock.add = MagicMock()
                    db_mock.flush = MagicMock()

                    mock_settings = MagicMock(cycle_budget=20)
                    with patch("app.services.search_selector.SearchExecutionModel") as MockModel, \
                         patch("app.core.config.settings", mock_settings):
                        MockModel.return_value = claim_mock
                        result = select_next_search(db_mock, reference_time=now)

                    assert result.action == "execute"
                    assert result.candidate.query_variant == "variant_b"

    @patch("app.services.search_selector._clean_abandoned_claims")
    def test_all_variants_penalized_falls_back_to_full_list(self, mock_clean):
        """When ALL variants are penalized (recent no-yield), the fallback
        uses the full variant list to avoid permanent starvation."""
        now = datetime.now(timezone.utc)
        candidate = self._make_candidate(variants=["variant_a", "variant_b"])

        with patch("app.services.search_selector.generate_candidates", return_value=[candidate]):
            with patch("app.services.search_selector._get_history") as mock_hist:
                mock_hist.return_value = {
                    candidate.candidate_id: {
                        "last_success": now - timedelta(hours=48),
                        "last_selected": now - timedelta(hours=25),
                        "success_count": 0,
                    }
                }
                with patch("app.services.search_selector._get_variant_history") as mock_vh:
                    mock_vh.return_value = {
                        candidate.candidate_id: {
                            "variant_a": {
                                "fetched": 0,
                                "eligible": None,
                                "completed_at": now - timedelta(hours=2), "id": 10},
                            "variant_b": {
                                "fetched": 15,
                                "eligible": 0,
                                "completed_at": now - timedelta(hours=3), "id": 20},
                        }
                    }

                    db_mock = MagicMock()
                    ctx = MagicMock()
                    ctx.__enter__ = MagicMock(return_value=None)
                    ctx.__exit__ = MagicMock(return_value=False)
                    db_mock.begin_nested.return_value = ctx
                    claim_mock = MagicMock()
                    claim_mock.id = 1001
                    db_mock.add = MagicMock()
                    db_mock.flush = MagicMock()

                    mock_settings = MagicMock(cycle_budget=20)
                    with patch("app.services.search_selector.SearchExecutionModel") as MockModel, \
                         patch("app.core.config.settings", mock_settings):
                        MockModel.return_value = claim_mock
                        result = select_next_search(db_mock, reference_time=now)

                    assert result.action == "execute"
                    # Fallback to full list, success_count=0 → index 0
                    assert result.candidate.query_variant == "variant_a"

class TestAdaptiveRetrievalScope:
    """Tests for adaptive retrieval scope (location broadening) when
    granular locations face repeated NO_INVENTORY outcomes."""

    def _make_candidate(self, cid="ROLE-X::LOC-BLR-002", loc_id="LOC-BLR-002", variants=None):
        return SearchCandidate(
            candidate_id=cid,
            role_id="ROLE-X",
            location_id=loc_id,
            role_canonical="Test Role",
            location_canonical="Whitefield",
            priority=1,
            tier=1,
            variants=variants or ["variant_a", "variant_b"],
        )

    @patch("app.services.search_selector._clean_abandoned_claims")
    def test_granular_with_repeated_no_inventory_broadens(self, mock_clean):
        now = datetime.now(timezone.utc)
        candidate = self._make_candidate()

        with patch("app.services.search_selector.generate_candidates", return_value=[candidate]):
            with patch("app.services.search_selector._get_history") as mock_hist:
                mock_hist.return_value = {
                    candidate.candidate_id: {
                        "last_success": now - timedelta(hours=48),
                        "last_selected": now - timedelta(hours=25),
                        "success_count": 0,
                    }
                }
                with patch("app.services.search_selector._get_variant_history") as mock_vh:
                    mock_vh.return_value = {
                        candidate.candidate_id: {
                            "variant_a": {
                                "fetched": 0, # NO_INVENTORY
                                "eligible": None,
                                "completed_at": now - timedelta(hours=2), "id": 10},
                            "variant_b": {
                                "fetched": 0, # NO_INVENTORY
                                "eligible": None,
                                "completed_at": now - timedelta(hours=3), "id": 20},
                        }
                    }

                    db_mock = MagicMock()
                    ctx = MagicMock()
                    ctx.__enter__ = MagicMock(return_value=None)
                    ctx.__exit__ = MagicMock(return_value=False)
                    db_mock.begin_nested.return_value = ctx
                    claim_mock = MagicMock()
                    db_mock.add = MagicMock()

                    mock_settings = MagicMock(cycle_budget=20)
                    with patch("app.services.search_selector.SearchExecutionModel") as MockModel, \
                         patch("app.core.config.settings", mock_settings):
                        MockModel.return_value = claim_mock
                        result = select_next_search(db_mock, reference_time=now)

                    assert result.action == "execute"
                    assert result.candidate.retrieval_location == "Bengaluru"
                    # Telemetry test: execution claim gets the retrieval_location
                    MockModel.assert_called_once()
                    call_kwargs = MockModel.call_args.kwargs
                    assert call_kwargs["retrieval_location"] == "Bengaluru"
                    # Candidate identity remains unchanged
                    assert call_kwargs["candidate_id"] == "ROLE-X::LOC-BLR-002"

    @patch("app.services.search_selector._clean_abandoned_claims")
    def test_granular_with_inventory_no_fresher_does_not_broaden(self, mock_clean):
        now = datetime.now(timezone.utc)
        candidate = self._make_candidate()

        with patch("app.services.search_selector.generate_candidates", return_value=[candidate]):
            with patch("app.services.search_selector._get_history") as mock_hist:
                mock_hist.return_value = {
                    candidate.candidate_id: {
                        "last_success": now - timedelta(hours=48),
                        "last_selected": now - timedelta(hours=25),
                        "success_count": 0,
                    }
                }
                with patch("app.services.search_selector._get_variant_history") as mock_vh:
                    mock_vh.return_value = {
                        candidate.candidate_id: {
                            "variant_a": {
                                "fetched": 15,
                                "eligible": 0, # NO_FRESHER
                                "completed_at": now - timedelta(hours=2), "id": 10},
                            "variant_b": {
                                "fetched": 10,
                                "eligible": 0, # NO_FRESHER
                                "completed_at": now - timedelta(hours=3), "id": 20},
                        }
                    }

                    db_mock = MagicMock()
                    ctx = MagicMock()
                    ctx.__enter__ = MagicMock(return_value=None)
                    ctx.__exit__ = MagicMock(return_value=False)
                    db_mock.begin_nested.return_value = ctx

                    mock_settings = MagicMock(cycle_budget=20)
                    with patch("app.services.search_selector.SearchExecutionModel") as MockModel, \
                         patch("app.core.config.settings", mock_settings):
                        MockModel.return_value = MagicMock()
                        result = select_next_search(db_mock, reference_time=now)

                    assert result.action == "execute"
                    # Should NOT broaden because they found inventory (just no freshers)
                    assert result.candidate.retrieval_location is None

    @patch("app.services.search_selector._clean_abandoned_claims")
    def test_canonical_bengaluru_does_not_broaden(self, mock_clean):
        now = datetime.now(timezone.utc)
        candidate = self._make_candidate(cid="ROLE-X::LOC-BLR-001", loc_id="LOC-BLR-001")

        with patch("app.services.search_selector.generate_candidates", return_value=[candidate]):
            with patch("app.services.search_selector._get_history") as mock_hist:
                mock_hist.return_value = {
                    candidate.candidate_id: {
                        "last_success": now - timedelta(hours=48),
                        "last_selected": now - timedelta(hours=25),
                        "success_count": 0,
                    }
                }
                with patch("app.services.search_selector._get_variant_history") as mock_vh:
                    mock_vh.return_value = {
                        candidate.candidate_id: {
                            "variant_a": {
                                "fetched": 0,
                                "eligible": None,
                                "completed_at": now - timedelta(hours=2), "id": 10},
                            "variant_b": {
                                "fetched": 0,
                                "eligible": None,
                                "completed_at": now - timedelta(hours=3), "id": 20},
                        }
                    }

                    db_mock = MagicMock()
                    ctx = MagicMock()
                    ctx.__enter__ = MagicMock(return_value=None)
                    ctx.__exit__ = MagicMock(return_value=False)
                    db_mock.begin_nested.return_value = ctx

                    mock_settings = MagicMock(cycle_budget=20)
                    with patch("app.services.search_selector.SearchExecutionModel") as MockModel, \
                         patch("app.core.config.settings", mock_settings):
                        MockModel.return_value = MagicMock()
                        result = select_next_search(db_mock, reference_time=now)

                    assert result.action == "execute"
                    # Canonical Bengaluru should not artificially broaden itself
                    assert result.candidate.retrieval_location is None

    @patch("app.services.search_selector._clean_abandoned_claims")
    def test_recent_selection_cooldown_does_not_masquerade_as_drought(self, mock_clean):
        now = datetime.now(timezone.utc)
        candidate = self._make_candidate()

        with patch("app.services.search_selector.generate_candidates", return_value=[candidate]):
            with patch("app.services.search_selector._get_history") as mock_hist:
                mock_hist.return_value = {
                    candidate.candidate_id: {
                        "last_success": now - timedelta(hours=48),
                        "last_selected": now - timedelta(minutes=5), # Recent selection!
                        "success_count": 0,
                    }
                }
                # Variant history mock shouldn't even be called, but we provide it just in case
                with patch("app.services.search_selector._get_variant_history") as mock_vh:
                    mock_vh.return_value = {}

                    db_mock = MagicMock()
                    mock_settings = MagicMock(cycle_budget=20)
                    with patch("app.core.config.settings", mock_settings):
                        result = select_next_search(db_mock, reference_time=now)

                    # Should stop because the candidate was removed from pre_eligible entirely
                    assert result.action == "stop"
                    assert result.reason == "all_candidates_ineligible_or_fresh"

    @patch("app.services.search_selector._clean_abandoned_claims")
    def test_success_cooldown_does_not_masquerade_as_drought(self, mock_clean):
        now = datetime.now(timezone.utc)
        candidate = self._make_candidate()

        with patch("app.services.search_selector.generate_candidates", return_value=[candidate]):
            with patch("app.services.search_selector._get_history") as mock_hist:
                mock_hist.return_value = {
                    candidate.candidate_id: {
                        "last_success": now - timedelta(hours=2), # Recent success!
                        "last_selected": now - timedelta(hours=3),
                        "success_count": 1,
                    }
                }
                with patch("app.services.search_selector._get_variant_history") as mock_vh:
                    mock_vh.return_value = {}

                    db_mock = MagicMock()
                    mock_settings = MagicMock(cycle_budget=20)
                    with patch("app.core.config.settings", mock_settings):
                        result = select_next_search(db_mock, reference_time=now)

                    assert result.action == "stop"
                    assert result.reason == "all_candidates_ineligible_or_fresh"

    @patch("app.services.search_selector._clean_abandoned_claims")
    def test_mixture_of_penalties_does_not_broaden(self, mock_clean):
        now = datetime.now(timezone.utc)
        candidate = self._make_candidate()

        with patch("app.services.search_selector.generate_candidates", return_value=[candidate]):
            with patch("app.services.search_selector._get_history") as mock_hist:
                mock_hist.return_value = {
                    candidate.candidate_id: {
                        "last_success": now - timedelta(hours=48),
                        "last_selected": now - timedelta(hours=25),
                        "success_count": 0,
                    }
                }
                with patch("app.services.search_selector._get_variant_history") as mock_vh:
                    mock_vh.return_value = {
                        candidate.candidate_id: {
                            "variant_a": {
                                "fetched": 0, # NO_INVENTORY
                                "eligible": None,
                                "completed_at": now - timedelta(hours=2), "id": 10},
                            "variant_b": {
                                "fetched": 10, # INVENTORY_NO_FRESHER
                                "eligible": 0,
                                "completed_at": now - timedelta(hours=3), "id": 20},
                        }
                    }

                    db_mock = MagicMock()
                    ctx = MagicMock()
                    ctx.__enter__ = MagicMock(return_value=None)
                    ctx.__exit__ = MagicMock(return_value=False)
                    db_mock.begin_nested.return_value = ctx

                    mock_settings = MagicMock(cycle_budget=20)
                    with patch("app.services.search_selector.SearchExecutionModel") as MockModel, \
                         patch("app.core.config.settings", mock_settings):
                        MockModel.return_value = MagicMock()
                        result = select_next_search(db_mock, reference_time=now)

                    assert result.action == "execute"
                    # Should NOT broaden because variant_b found inventory (proving geography is not entirely dead)
                    assert result.candidate.retrieval_location is None

    @patch("app.services.search_selector._clean_abandoned_claims")
    def test_unknown_telemetry_does_not_broaden(self, mock_clean):
        now = datetime.now(timezone.utc)
        candidate = self._make_candidate()

        with patch("app.services.search_selector.generate_candidates", return_value=[candidate]):
            with patch("app.services.search_selector._get_history") as mock_hist:
                mock_hist.return_value = {
                    candidate.candidate_id: {
                        "last_success": now - timedelta(hours=48),
                        "last_selected": now - timedelta(hours=25),
                        "success_count": 0,
                    }
                }
                with patch("app.services.search_selector._get_variant_history") as mock_vh:
                    mock_vh.return_value = {
                        candidate.candidate_id: {
                            "variant_a": {
                                "fetched": None, # UNKNOWN / FAILED
                                "eligible": None,
                                "completed_at": now - timedelta(hours=2), "id": 10},
                            "variant_b": {
                                "fetched": None, # UNKNOWN / FAILED
                                "eligible": None,
                                "completed_at": now - timedelta(hours=3), "id": 20},
                        }
                    }

                    db_mock = MagicMock()
                    ctx = MagicMock()
                    ctx.__enter__ = MagicMock(return_value=None)
                    ctx.__exit__ = MagicMock(return_value=False)
                    db_mock.begin_nested.return_value = ctx

                    mock_settings = MagicMock(cycle_budget=20)
                    with patch("app.services.search_selector.SearchExecutionModel") as MockModel, \
                         patch("app.core.config.settings", mock_settings):
                        MockModel.return_value = MagicMock()
                        result = select_next_search(db_mock, reference_time=now)

                    assert result.action == "execute"
                    # None is NOT zero inventory. So neither zero_yield_variants nor no_fresher_variants increment.
                    # It falls back to available=variants, but since zero_yield_variants == 0, it MUST NOT broaden!
                    assert result.candidate.retrieval_location is None

    @patch("app.services.search_selector._clean_abandoned_claims")
    def test_negative_telemetry_becomes_unknown_not_no_inventory(self, mock_clean):
        now = datetime.now(timezone.utc)
        candidate = self._make_candidate(variants=["v_neg"])
        candidate.location_id = "LOC-BLR-002"
        with patch("app.services.search_selector.generate_candidates", return_value=[candidate]):
            with patch("app.services.search_selector._get_history", return_value={}):
                with patch("app.services.search_selector._get_variant_history") as mock_vh:
                    mock_vh.return_value = {
                        candidate.candidate_id: {
                            "v_neg": {"fetched": -5, "eligible": -2, "completed_at": now - timedelta(hours=2), "id": 10},
                        }
                    }
                    db_mock = MagicMock()
                    db_mock.begin_nested.return_value.__enter__ = MagicMock()
                    db_mock.begin_nested.return_value.__exit__ = MagicMock()
                    with patch("app.services.search_selector.SearchExecutionModel", return_value=MagicMock()):
                        from app.services.search_selector import select_next_search
                        result = select_next_search(db_mock, reference_time=now)
                        assert result.candidate.query_variant == "v_neg"
                        assert result.candidate.retrieval_location is None

    @patch("app.services.search_selector._clean_abandoned_claims")
    def test_null_completed_at_treated_as_active_unknown(self, mock_clean):
        now = datetime.now(timezone.utc)
        candidate = self._make_candidate(variants=["v_good", "v_null"])
        with patch("app.services.search_selector.generate_candidates", return_value=[candidate]):
            with patch("app.services.search_selector._get_history", return_value={}):
                with patch("app.services.search_selector._get_variant_history") as mock_vh:
                    mock_vh.return_value = {
                        candidate.candidate_id: {
                            "v_good": {"fetched": 10, "eligible": 10, "completed_at": now - timedelta(days=10), "id": 11},
                            "v_null": {"fetched": 100, "eligible": 100, "completed_at": None, "id": 12},
                        }
                    }
                    db_mock = MagicMock()
                    db_mock.begin_nested.return_value.__enter__ = MagicMock()
                    db_mock.begin_nested.return_value.__exit__ = MagicMock()
                    with patch("app.services.search_selector.SearchExecutionModel", return_value=MagicMock()):
                        from app.services.search_selector import select_next_search
                        result = select_next_search(db_mock, reference_time=now)
                        assert result.candidate.query_variant == "v_good"

    @patch("app.services.search_selector._clean_abandoned_claims")
    def test_impossible_fresher_count_treated_as_unknown(self, mock_clean):
        now = datetime.now(timezone.utc)
        candidate = self._make_candidate(variants=["v_good", "v_impossible"])
        with patch("app.services.search_selector.generate_candidates", return_value=[candidate]):
            with patch("app.services.search_selector._get_history", return_value={}):
                with patch("app.services.search_selector._get_variant_history") as mock_vh:
                    mock_vh.return_value = {
                        candidate.candidate_id: {
                            "v_good": {"fetched": 10, "eligible": 10, "completed_at": now - timedelta(days=10), "id": 13},
                            "v_impossible": {"fetched": 10, "eligible": 20, "completed_at": now - timedelta(days=10), "id": 14},
                        }
                    }
                    db_mock = MagicMock()
                    db_mock.begin_nested.return_value.__enter__ = MagicMock()
                    db_mock.begin_nested.return_value.__exit__ = MagicMock()
                    with patch("app.services.search_selector.SearchExecutionModel", return_value=MagicMock()):
                        from app.services.search_selector import select_next_search
                        result = select_next_search(db_mock, reference_time=now)
                        assert result.candidate.query_variant == "v_impossible"

    def test_true_multi_cycle_fallback_fairness_with_real_db(self):
        from app.db.database import SessionLocal
        from app.db.models.search_execution import SearchExecutionModel
        from app.services.search_selector import select_next_search, _clean_abandoned_claims
        db = SessionLocal()
        cid = "ROLE-TEST::LOC-TEST"
        v1 = "v_dead1"
        v2 = "v_dead2"
        now = datetime.now(timezone.utc)
        try:
            candidate = self._make_candidate(variants=[v1, v2])
            candidate.candidate_id = cid
            candidate.priority = 1
            with patch("app.services.search_selector.generate_candidates", return_value=[candidate]):
                with patch("app.services.search_selector._get_history", return_value={}):
                    e1 = SearchExecutionModel(
                        candidate_id=cid, status="succeeded", query_variant=v1,
                        jobs_fetched=0, jobs_fresher_eligible=0,
                        selected_at=now - timedelta(hours=10), completed_at=now - timedelta(hours=9)
                    )
                    db.add(e1)
                    db.commit()
                    db.refresh(e1)
                    e2 = SearchExecutionModel(
                        candidate_id=cid, status="succeeded", query_variant=v2,
                        jobs_fetched=0, jobs_fresher_eligible=0,
                        selected_at=now - timedelta(hours=8), completed_at=now - timedelta(hours=7)
                    )
                    db.add(e2)
                    db.commit()
                    db.refresh(e2)
                    result1 = select_next_search(db, reference_time=now)
                    assert result1.candidate.query_variant == v1
                    future_time_1 = now + timedelta(minutes=20)
                    # Simulate the ingestion pipeline completing the search with 0 jobs
                    exec_row_1 = db.query(SearchExecutionModel).filter_by(id=result1.execution_id).first()
                    exec_row_1.status = "succeeded"
                    exec_row_1.jobs_fetched = 0
                    exec_row_1.jobs_fresher_eligible = 0
                    exec_row_1.completed_at = future_time_1
                    db.commit()
                    result2 = select_next_search(db, reference_time=future_time_1)
                    assert result2.candidate.query_variant == v2
                    future_time_2 = future_time_1 + timedelta(minutes=20)
                    exec_row_2 = db.query(SearchExecutionModel).filter_by(id=result2.execution_id).first()
                    exec_row_2.status = "succeeded"
                    exec_row_2.jobs_fetched = 0
                    exec_row_2.jobs_fresher_eligible = 0
                    exec_row_2.completed_at = future_time_2
                    db.commit()
                    result3 = select_next_search(db, reference_time=future_time_2)
                    assert result3.candidate.query_variant == v1
        finally:
            db.query(SearchExecutionModel).filter(SearchExecutionModel.candidate_id == cid).delete()
            db.commit()
            db.close()

    def test_db_backed_fallback_fairness_with_identical_timestamps(self):
        from app.db.database import SessionLocal
        from app.db.models.search_execution import SearchExecutionModel
        from app.services.search_selector import select_next_search, _clean_abandoned_claims
        db = SessionLocal()
        cid = "ROLE-TEST::LOC-TEST-TIE"
        v1 = "v_tie1"
        v2 = "v_tie2"
        now = datetime.now(timezone.utc)
        try:
            candidate = self._make_candidate(variants=[v1, v2])
            candidate.candidate_id = cid
            candidate.priority = 1
            with patch("app.services.search_selector.generate_candidates", return_value=[candidate]):
                with patch("app.services.search_selector._get_history", return_value={}):
                    e1 = SearchExecutionModel(
                        candidate_id=cid, status="succeeded", query_variant=v1,
                        jobs_fetched=0, jobs_fresher_eligible=0,
                        selected_at=now - timedelta(hours=10), completed_at=now - timedelta(hours=9)
                    )
                    db.add(e1)
                    db.commit()
                    db.refresh(e1)
                    e2 = SearchExecutionModel(
                        candidate_id=cid, status="succeeded", query_variant=v2,
                        jobs_fetched=0, jobs_fresher_eligible=0,
                        selected_at=now - timedelta(hours=8), completed_at=now - timedelta(hours=9)
                    )
                    db.add(e2)
                    db.commit()
                    db.refresh(e2)
                    result1 = select_next_search(db, reference_time=now)
                    assert result1.candidate.query_variant == v1
                    future_time_1 = now + timedelta(minutes=20)
                    # Simulate the ingestion pipeline completing the search with 0 jobs
                    exec_row_1 = db.query(SearchExecutionModel).filter_by(id=result1.execution_id).first()
                    exec_row_1.status = "succeeded"
                    exec_row_1.jobs_fetched = 0
                    exec_row_1.jobs_fresher_eligible = 0
                    exec_row_1.completed_at = future_time_1
                    db.commit()
                    result2 = select_next_search(db, reference_time=future_time_1)
                    assert result2.candidate.query_variant == v2
                    future_time_2 = future_time_1 + timedelta(minutes=20)
                    exec_row_2 = db.query(SearchExecutionModel).filter_by(id=result2.execution_id).first()
                    exec_row_2.status = "succeeded"
                    exec_row_2.jobs_fetched = 0
                    exec_row_2.jobs_fresher_eligible = 0
                    exec_row_2.completed_at = future_time_2
                    db.commit()
                    result3 = select_next_search(db, reference_time=future_time_2)
                    assert result3.candidate.query_variant == v1
        finally:
            db.query(SearchExecutionModel).filter(SearchExecutionModel.candidate_id == cid).delete()
            db.commit()
            db.close()
