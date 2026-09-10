import concurrent.futures
import threading
import uuid
import pytest
from datetime import datetime, timezone
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from app.db.database import SessionLocal, engine
from app.db.models.search_execution import SearchExecutionModel, SearchCycleUsageModel
from app.domain.candidate import SearchCandidate
from app.services.search_selector import select_next_search, CycleBudgetExhausted
from app.core.config import settings

def test_cycle_race_exactly_one_claim(monkeypatch):
    cycle_id = str(uuid.uuid4())
    candidate = SearchCandidate(
        candidate_id="ROLE-TEST::CYCLE-LOC-TEST",
        role_id="ROLE-TEST",
        location_id="CYCLE-LOC-TEST",
        role_canonical="Test Role",
        location_canonical="Test Location",
        priority=1,
        tier=0,
    )
    monkeypatch.setattr("app.services.search_selector.generate_candidates", lambda: [candidate])

    db = SessionLocal()
    db.query(SearchExecutionModel).filter_by(candidate_id=candidate.candidate_id).delete()
    db.commit()
    db.close()

    barrier = threading.Barrier(2)

    waited = set()
    def hook(_):
        tid = threading.get_ident()
        if tid not in waited:
            waited.add(tid)
            try:
                barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass


    def worker():
        session = SessionLocal()
        try:
            result = select_next_search(
                session,
                before_claim=hook,
                cycle_id=cycle_id
            )
            return result
        finally:
            session.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: worker(), range(2)))

    # Only one should succeed
    successes = [r for r in results if r.action == "execute"]
    stops = [r for r in results if r.action == "stop"]
    assert len(successes) == 1
    assert len(stops) == 1

    # Check database constraints
    db = SessionLocal()
    count = db.query(SearchCycleUsageModel).filter_by(cycle_id=cycle_id).first().execution_count
    assert count == 1 # Exactly one durable cycle slot
    claims = db.query(SearchExecutionModel).filter_by(cycle_id=cycle_id).count()
    assert claims == 1
    db.close()

def test_cycle_budget_exhaustion(monkeypatch):
    settings.cycle_budget = 2
    cycle_id = str(uuid.uuid4())
    candidates = [
        SearchCandidate(
            candidate_id=f"ROLE-TEST-EX::LOC-{i}-{uuid.uuid4()}",
            role_id="ROLE-TEST",
            location_id=f"LOC-{i}",
            role_canonical="Test Role",
            location_canonical="Test Location",
            priority=1,
            tier=0,
        ) for i in range(5)
    ]
    monkeypatch.setattr("app.services.search_selector.generate_candidates", lambda: candidates)

    barrier = threading.Barrier(5)

    waited = set()
    def hook(_):
        tid = threading.get_ident()
        if tid not in waited:
            waited.add(tid)
            try:
                barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass


    def worker():
        session = SessionLocal()
        try:
            result = select_next_search(
                session,
                before_claim=hook,
                cycle_id=cycle_id
            )
            return result
        finally:
            session.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(lambda _: worker(), range(5)))

    successes = [r for r in results if r.action == "execute"]
    stops = [r for r in results if r.action == "stop"]
    assert len(successes) == 2
    assert len(stops) == 3

    db = SessionLocal()
    usage = db.query(SearchCycleUsageModel).filter_by(cycle_id=cycle_id).first()
    assert usage.execution_count == 2
    claims = db.query(SearchExecutionModel).filter_by(cycle_id=cycle_id).count()
    assert claims == 2
    db.close()

def test_different_cycles_independent(monkeypatch):
    c1 = str(uuid.uuid4())
    c2 = str(uuid.uuid4())
    
    cand1 = SearchCandidate(candidate_id=f"ROLE-IND::1-{uuid.uuid4()}", role_id="ROLE", location_id="1", role_canonical="R", location_canonical="L", priority=1, tier=0)
    cand2 = SearchCandidate(candidate_id=f"ROLE-IND::2-{uuid.uuid4()}", role_id="ROLE", location_id="2", role_canonical="R", location_canonical="L", priority=1, tier=0)
    
    # We must ensure they pick different candidates, or just rely on the concurrency handling. 
    # If they pick the same, one will fail and pick the next.
    
    monkeypatch.setattr("app.services.search_selector.generate_candidates", lambda: [cand1, cand2])

    barrier = threading.Barrier(2)

    waited = set()
    def hook(_):
        tid = threading.get_ident()
        if tid not in waited:
            waited.add(tid)
            try:
                barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass


    def worker(cid):
        session = SessionLocal()
        try:
            result = select_next_search(
                session,
                before_claim=hook,
                cycle_id=cid
            )
            return result
        finally:
            session.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(worker, [c1, c2]))

    print(f"\nRESULTS: {results}\n"); assert all(r.action == "execute" for r in results)
    
    db = SessionLocal()
    assert db.query(SearchCycleUsageModel).filter_by(cycle_id=c1).first().execution_count == 1
    assert db.query(SearchCycleUsageModel).filter_by(cycle_id=c2).first().execution_count == 1
    db.close()

def test_n8n_continue_on_fail_contract(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services.provider_router import ProviderSelectionResult
    from app.providers.types import ProviderName
    
    # We must mock router and execute to force 502, but verify select-next gives another candidate
    client = TestClient(app)
    from app.core.config import settings
    api_key = settings.api_secret_key
    
    # Fresh cycle
    cycle_id = str(uuid.uuid4())
    
    cand1 = SearchCandidate(candidate_id=f"MOCK::1-{uuid.uuid4()}", role_id="MOCK", location_id="1", role_canonical="M", location_canonical="1", priority=1, tier=0)
    cand2 = SearchCandidate(candidate_id=f"MOCK::2-{uuid.uuid4()}", role_id="MOCK", location_id="2", role_canonical="M", location_canonical="2", priority=1, tier=0)
    
    monkeypatch.setattr("app.services.search_selector.generate_candidates", lambda: [cand1, cand2])
    monkeypatch.setattr("app.services.provider_router.route_provider", lambda db: ProviderSelectionResult(provider=ProviderName.ADZUNA, reason="test", policy_version="v1"))
    
    # Loop iteration 1
    res1 = client.post("/ingestion/internal/select-next", json={"cycle_id": cycle_id}, headers={"x-api-key": api_key})
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["action"] == "execute"
    assert data1["candidate_id"] == cand1.candidate_id
    
    intent = data1["intent"]
    
    # Fake provider fail via 502 (Actually just mocking run_ingestion to raise ProviderExecutionError or just returning 502 directly)
    # We can mock run_ingestion to return a failed result
    from app.services.ingestion import IngestionResult
    def mock_run_ingestion(db, provider_name, provider_client, query, execution_id=None):
        from sqlalchemy import text
        db.execute(text("UPDATE search_execution SET status='failed' WHERE id=:id"), {"id": execution_id})
        db.commit()
        return IngestionResult(provider=ProviderName.ADZUNA, fetched=0, created=0, duplicates=0, invalid=0, failed=1)
    monkeypatch.setattr("app.api.endpoints.ingestion.run_ingestion", mock_run_ingestion)
    
    res_exec = client.post("/ingestion/internal/search", json=intent, headers={"x-api-key": api_key})
    print(res_exec.json()); assert res_exec.status_code == 502 # Prove it returns 502 as expected
    
    # Loop iteration 2 (n8n "Continue on fail" catches the 502 and loops)
    res2 = client.post("/ingestion/internal/select-next", json={"cycle_id": cycle_id}, headers={"x-api-key": api_key})
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["action"] == "execute"
    assert data2["candidate_id"] == cand2.candidate_id
    # We successfully moved to candidate 2 within the same cycle!
    
    # We can check budget count
    db = SessionLocal()
    assert db.query(SearchCycleUsageModel).filter_by(cycle_id=cycle_id).first().execution_count == 2
    
    # Verify execution 1 was indeed marked failed and execution 2 is selected
    ex1 = db.query(SearchExecutionModel).filter_by(id=data1["execution_id"]).first()
    assert ex1.status == "failed"
    ex2 = db.query(SearchExecutionModel).filter_by(id=data2["execution_id"]).first()
    assert ex2.status == "selected"
    db.close()

def test_no_eligible_candidate(monkeypatch):
    cycle_id = str(uuid.uuid4())
    # No candidates generated
    monkeypatch.setattr("app.services.search_selector.generate_candidates", lambda: [])

    db = SessionLocal()
    result = select_next_search(db, cycle_id=cycle_id)
    
    assert result.action == "stop"
    assert result.reason == "all_candidates_ineligible_or_fresh"
    
    # Prove cycle usage is completely unchanged (no row created)
    usage = db.query(SearchCycleUsageModel).filter_by(cycle_id=cycle_id).first()
    assert usage is None
    db.close()

def test_n8n_workflow_contract():
    import json
    import os
    workflow_path = os.path.join(os.path.dirname(__file__), "../../JP___Search_Cycle.json")
    with open(workflow_path, "r") as f:
        data = json.load(f)
    
    workflow = data[0]
    nodes = workflow["nodes"]
    
    loop_nodes = [n for n in nodes if n["type"] == "n8n-nodes-base.loop"]
    assert len(loop_nodes) == 1, "Exactly one Loop node required"
    
    select_nodes = [n for n in nodes if n["name"] == "Select Next"]
    assert len(select_nodes) == 1, "Exactly one Select Next HTTP node required"
    select_node = select_nodes[0]
    assert select_node["type"] == "n8n-nodes-base.httpRequest"
    assert "cycle_id" in select_node["parameters"]["jsonBody"], "Select-next must send cycle_id"
    assert "budget" not in select_node["parameters"]["jsonBody"], "Select-next must not send budget"
    
    exec_nodes = [n for n in nodes if n["name"] == "Execute"]
    assert len(exec_nodes) == 1, "Exactly one Execute HTTP node required"
    exec_node = exec_nodes[0]
    assert exec_node["type"] == "n8n-nodes-base.httpRequest"
    assert exec_node.get("continueOnFail") is True, "Execute node MUST have continueOnFail enabled"
    
    # Check connections
    conns = workflow["connections"]
    
    # Action Check -> Execute (execute branch)
    action_check_conns = conns["Action Check"]["main"]
    assert action_check_conns[0][0]["node"] == "Execute", "Action Check branch 0 must go to Execute"
    assert action_check_conns[1][0]["node"] == "Stop", "Action Check branch 1 must go to Stop"
    
    # Execute -> Loop
    execute_conns = conns["Execute"]["main"]
    assert execute_conns[0][0]["node"] == "Loop", "Execute must route back to Loop"
    
    # Ensure no provider credentials or names hardcoded in workflow
    json_str = json.dumps(workflow)
    assert "adzuna" not in json_str.lower(), "No hardcoded provider names allowed in workflow"
    assert "jooble" not in json_str.lower(), "No hardcoded provider names allowed in workflow"
