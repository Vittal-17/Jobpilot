import json
from pathlib import Path


def test_execute_workflow_carries_selection_execution_id():
    workflows = json.loads(
        Path("backend/JP___Execute_One_Search.json").read_text(encoding="utf-8")
    )
    assert len(workflows) == 1
    workflow = workflows[0]
    nodes = {node["name"]: node for node in workflow["nodes"]}

    selector = nodes["Select Next via FastAPI"]
    executor = nodes["Execute via FastAPI"]
    branch = nodes["Candidate Selected"]

    assert selector["parameters"]["url"].endswith("/ingestion/internal/select-next")
    assert executor["parameters"]["url"].endswith("/ingestion/internal/search")
    assert executor["parameters"]["jsonBody"] == "={{ JSON.stringify($json.intent) }}"
    assert "execution_id" in branch["parameters"]["conditions"]["conditions"][0]["leftValue"]
    assert "intent.provider === $json.provider" in branch["parameters"]["conditions"]["conditions"][0]["leftValue"]
    assert workflow["active"] is False

    connections = workflow["connections"]
    assert connections["Candidate Selected"]["main"][0][0]["node"] == "Execute via FastAPI"
    assert connections["Candidate Selected"]["main"][1][0]["node"] == "No Candidate"

    serialized = json.dumps(workflow).lower()
    assert "adzuna_app_key" not in serialized
    assert "jooble_api_key" not in serialized
    assert '"provider":"adzuna"' not in serialized.replace(" ", "")
    assert '"provider":"jooble"' not in serialized.replace(" ", "")
    assert serialized.count("/ingestion/internal/search") == 1
