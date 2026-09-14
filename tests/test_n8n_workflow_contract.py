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

def test_notifications_workflow_contract():
    workflows = json.loads(
        Path("backend/JP___Notifications.json").read_text(encoding="utf-8")
    )
    assert len(workflows) == 1
    workflow = workflows[0]
    nodes = {node["name"]: node for node in workflow["nodes"]}

    assert "Schedule" in nodes
    assert "Claim Notifications" in nodes
    assert "Check If Empty" in nodes
    assert "Format Payload" in nodes
    assert "Send Telegram" in nodes
    assert "Acknowledge Delivery" in nodes

    claim = nodes["Claim Notifications"]
    assert claim["parameters"]["url"].endswith("/ingestion/internal/notifications/claim")
    assert "delivery_id" in claim["parameters"]["jsonBody"]
    assert "$execution.id" in claim["parameters"]["jsonBody"]

    ack = nodes["Acknowledge Delivery"]
    assert ack["parameters"]["url"].endswith("/ingestion/internal/notifications/acknowledge")
    assert "delivery_id" in ack["parameters"]["jsonBody"]
    assert "$execution.id" in ack["parameters"]["jsonBody"]

    check_empty = nodes["Check If Empty"]
    assert check_empty["parameters"]["conditions"]["conditions"][0]["leftValue"] == "={{ $json.recommendations.length }}"
    assert check_empty["parameters"]["conditions"]["conditions"][0]["rightValue"] == 0

    assert workflow["active"] is False

def test_notifications_workflow_escaping():
    import subprocess
    import tempfile

    workflows = json.loads(
        Path("backend/JP___Notifications.json").read_text(encoding="utf-8")
    )
    workflow = workflows[0]
    nodes = {node["name"]: node for node in workflow["nodes"]}
    js_code = nodes["Format Payload"]["parameters"]["jsCode"]

    # Mock the $input.first().json
    test_data = {
        "user_id": 1,
        "delivery_id": "test_del",
        "recommendations": [
            {
                "url": "https://example.com/job?id=1&ref=abc\"def",
                "title": "Senior <Backend> Engineer & Tech Lead",
                "company": "Company \"A\" > B",
                "location": "O'Reilly & Sons, NY"
            }
        ]
    }

    wrapper = f"""
    const $input = {{
        first: () => ({{
            json: {json.dumps(test_data)}
        }})
    }};

    function run() {{
        {js_code}
    }}

    console.log(JSON.stringify(run()));
    """

    with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
        f.write(wrapper)
        temp_path = f.name

    try:
        result = subprocess.run(["node", temp_path], capture_output=True, text=True, check=True)
        output = json.loads(result.stdout)
        msg = output["json"]["message"]

        # Check escapes
        assert "Senior &lt;Backend&gt; Engineer &amp; Tech Lead" in msg
        assert "Company &quot;A&quot; &gt; B" in msg
        assert "O&#039;Reilly &amp; Sons" in msg
        assert "https://example.com/job?id=1&amp;ref=abc%22def" in msg
    finally:
        Path(temp_path).unlink()
