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

    # Prove the required UI schema fields exist in options
    options = check_empty["parameters"]["conditions"]["options"]
    assert options.get("version") == 2, "Filter component requires version: 2 for typeVersion 2.2 UI rendering"
    assert "leftValue" in options, "Filter component expects a global leftValue in options"

    condition = check_empty["parameters"]["conditions"]["conditions"][0]
    assert condition["leftValue"] == "={{ $json.recommendations.length }}"
    assert condition["rightValue"] == 0
    assert condition.get("id") is not None, "Check If Empty condition must have an 'id' to be recognized by n8n UI"
    assert condition.get("operator", {}).get("type") == "number"
    assert condition.get("operator", {}).get("operation") == "gt"

    assert workflow["active"] is False

    # Failure-path connectivity: ensure errors halt the workflow and never leak to the next node
    assert claim.get("continueOnFail") is not True
    assert nodes["Send Telegram"].get("continueOnFail") is not True
    assert ack.get("continueOnFail") is not True

    # Retry settings improve deterministic retry behavior natively for idempotent API boundaries
    assert claim.get("retryOnFail") is True
    assert ack.get("retryOnFail") is True

    # Telegram node MUST NOT have retryOnFail. While delivery_id provides idempotent durable claiming,
    # it does not guarantee external exactly-once Telegram delivery. An automatic retry on a lost response
    # would result in duplicate messages reaching the user.
    assert nodes["Send Telegram"].get("retryOnFail") is not True

    # Successful ordering and empty-path halting
    connections = workflow["connections"]
    assert connections["Claim Notifications"]["main"][0][0]["node"] == "Check If Empty"
    assert connections["Check If Empty"]["main"][0][0]["node"] == "Format Payload"
    assert len(connections["Check If Empty"]["main"]) == 2
    assert len(connections["Check If Empty"]["main"][1]) == 0  # false path stops
    assert connections["Format Payload"]["main"][0][0]["node"] == "Send Telegram"
    assert connections["Send Telegram"]["main"][0][0]["node"] == "Acknowledge Delivery"

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

def test_telegram_node_contract():
    import json
    from pathlib import Path
    workflows = json.loads(Path("backend/JP___Notifications.json").read_text(encoding="utf-8"))
    workflow = workflows[0]
    nodes = workflow.get("nodes", [])

    telegram_nodes = [n for n in nodes if n.get("type") == "n8n-nodes-base.telegram"]
    assert len(telegram_nodes) == 1, "Expected exactly one Telegram node"
    t_node = telegram_nodes[0]

    # Verify runtime variable wiring and no hardcoded chat ID
    params = t_node.get("parameters", {})
    assert params.get("chatId") == "={{ $env.TELEGRAM_CHAT_ID }}", "chatId must be dynamically wired to environment"
    assert params.get("additionalFields", {}).get("parse_mode") == "HTML", "Telegram node must strictly configure parse_mode=HTML inside additionalFields"

    # Verify no hardcoded token in the file
    workflow_str = json.dumps(workflow)
    import re
    # Match common telegram bot token format (e.g. 123456789:ABCDefGHIJK...)
    assert not re.search(r'\d{8,10}:[a-zA-Z0-9_-]{35}', workflow_str), "Workflow contains a hardcoded Telegram bot token!"

    # Verify no-retry behavior on Telegram node
    retry_settings = t_node.get("retryOnFail", False)
    assert retry_settings is False, "Telegram node MUST NOT automatically retry to preserve at-most-once delivery"

    # Verify connectivity: Claim -> Format -> Telegram -> Ack
    # We trace connections from the JSON
    connections = workflow.get("connections", {})

    # 'Format Payload' -> 'Send Telegram'
    format_conn = connections.get("Format Payload", {}).get("main", [])
    assert any(c.get("node") == "Send Telegram" for c in format_conn[0]), "Format Payload must connect to Send Telegram"

    # 'Send Telegram' -> 'Acknowledge Delivery'
    t_conn = connections.get("Send Telegram", {}).get("main", [])
    assert any(c.get("node") == "Acknowledge Delivery" for c in t_conn[0]), "Send Telegram must connect to Acknowledge Delivery"

def test_notifications_workflow_condition_evaluation():
    import subprocess
    import tempfile

    workflows = json.loads(
        Path("backend/JP___Notifications.json").read_text(encoding="utf-8")
    )
    workflow = workflows[0]
    nodes = {node["name"]: node for node in workflow["nodes"]}
    condition_def = nodes["Check If Empty"]["parameters"]["conditions"]["conditions"][0]

    # We want to test that leftValue ({{ $json.recommendations.length }}) > rightValue (0)
    # n8n evaluates {{ expression }} as JavaScript.

    left_expression = condition_def["leftValue"].strip("=").strip("{}").strip()
    right_value = condition_def["rightValue"]

    # We will wrap it in a Node.js script to evaluate the expression against empty and non-empty arrays
    wrapper = f"""
    function evaluate(payload) {{
        const $json = payload;
        const leftValue = {left_expression};
        return leftValue > {right_value};
    }}

    console.log(JSON.stringify({{
        empty: evaluate({{ recommendations: [] }}),
        one: evaluate({{ recommendations: [{{ id: 1 }}] }})
    }}));
    """

    with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
        f.write(wrapper)
        temp_path = f.name

    try:
        result = subprocess.run(["node", temp_path], capture_output=True, text=True, check=True)
        output = json.loads(result.stdout)
        assert output["empty"] is False, "Condition must evaluate to FALSE for 0 recommendations"
        assert output["one"] is True, "Condition must evaluate to TRUE for >0 recommendations"
    finally:
        Path(temp_path).unlink()
