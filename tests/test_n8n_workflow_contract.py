import json
from pathlib import Path


def test_search_cycle_workflow_contract():
    workflows = json.loads(
        Path("backend/JP___Search_Cycle.json").read_text(encoding="utf-8")
    )
    assert len(workflows) == 1
    workflow = workflows[0]
    nodes = {node["name"]: node for node in workflow["nodes"]}

    assert "Loop" in nodes
    assert "Select Next" in nodes
    assert "Action Check" in nodes
    assert "Execute" in nodes

    selector = nodes["Select Next"]
    action_check = nodes["Action Check"]
    executor = nodes["Execute"]

    assert selector["parameters"]["url"].endswith("/ingestion/internal/select-next")
    assert "cycle_id" in selector["parameters"]["jsonBody"]
    assert "$execution.id" in selector["parameters"]["jsonBody"]

    assert action_check["parameters"]["conditions"]["conditions"][0]["leftValue"] == "={{ $json.action }}"
    assert action_check["parameters"]["conditions"]["conditions"][0]["rightValue"] == "execute"

    assert executor["parameters"]["url"].endswith("/ingestion/internal/search")
    assert executor["parameters"]["jsonBody"] == "={{ JSON.stringify($json.intent) }}"
    assert executor.get("continueOnFail") is True

    assert workflow["active"] is False

    connections = workflow["connections"]
    assert connections["Action Check"]["main"][0][0]["node"] == "Execute"
    assert connections["Action Check"]["main"][1][0]["node"] == "Stop"
    assert connections["Execute"]["main"][0][0]["node"] == "Loop"

    serialized = json.dumps(workflow).lower()
    assert "adzuna_app_key" not in serialized
    assert "jooble_api_key" not in serialized
    assert '"provider":"adzuna"' not in serialized.replace(" ", "")
    assert '"provider":"jooble"' not in serialized.replace(" ", "")
    assert serialized.count("/ingestion/internal/search") == 1
    assert "execute_one_search" not in serialized
