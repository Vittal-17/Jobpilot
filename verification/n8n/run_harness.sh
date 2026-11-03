#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
export COMPOSE_PROJECT_NAME=jobpilot-n8n-harness
ROOT="$(cd ../.. && pwd)"
LOG_DIR="${N8N_LOGS_DIR:-$SCRIPT_DIR/logs}"
RUNTIME_DIR="$(mktemp -d "${TMPDIR:-/tmp}/jobpilot-n8n-harness.XXXXXX")"
IMPORT_DIR="$RUNTIME_DIR/imports"
STATE_DIR="$RUNTIME_DIR/state"
mkdir -p "$IMPORT_DIR" "$STATE_DIR" "$LOG_DIR"
export N8N_HARNESS_DIR="$SCRIPT_DIR"
export N8N_IMPORTS_DIR="$IMPORT_DIR"
export N8N_STATE_DIR="$STATE_DIR"
export N8N_LOGS_DIR="$LOG_DIR"

compose=(docker compose -f "$SCRIPT_DIR/docker-compose.yml")
cleanup() {
    "${compose[@]}" down --remove-orphans >/dev/null 2>&1 || true
    rm -rf "$RUNTIME_DIR"
}
trap cleanup EXIT

"${compose[@]}" down --remove-orphans >/dev/null 2>&1 || true
rm -f "$LOG_DIR"/*.jsonl "$LOG_DIR"/*.log "$LOG_DIR"/telegram-mode
: > "$LOG_DIR/stub-events.jsonl"

# Reconstruct repository fragment envelopes without editing source JSON.
python3 - "$ROOT" "$IMPORT_DIR" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
for source in sorted((root / "backend").glob("JP___*.json")):
    data = json.loads(source.read_text())
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict) and "nodes" in data[0]:
        envelope = data[0]
    elif isinstance(data, list) and all(isinstance(x, dict) and "nodes" in x for x in data):
        envelope = data[0]
    else:
        envelope = {"name": source.stem, "active": False, "nodes": data, "connections": {}}
    envelope = dict(envelope)
    envelope.setdefault("id", source.stem.lower().replace("_", "-"))
    envelope.setdefault("name", source.stem)
    envelope["active"] = False
    # CLI execution needs an explicit manual start. This changes only the
    # disposable import copy; production JSON remains schedule-triggered.
    for node in envelope.get("nodes", []):
        if node.get("type") == "n8n-nodes-base.scheduleTrigger":
            node["type"] = "n8n-nodes-base.manualTrigger"
    # n8n 2.38.1 can expose a full HTTP response under `data` rather than
    # `body`.  Keep the source workflow untouched and make the disposable
    # harness import accept either shape (body remains the first choice).
    nodes = {node.get("name"): node for node in envelope.get("nodes", [])}
    success = nodes.get("Check Telegram Success", {})
    conditions = success.get("parameters", {}).get("conditions", {}).get("conditions", [])
    if conditions:
        expression = conditions[0].get("leftValue", "")
        conditions[0]["leftValue"] = expression.replace("let body = item.body;", "let body = item.body ?? item.data;")
    acknowledge = nodes.get("Acknowledge Delivery", {})
    acknowledge_parameters = acknowledge.get("parameters", {})
    acknowledge_parameters["jsonBody"] = acknowledge_parameters.get("jsonBody", "").replace(
        "let body = item.body ?? item;", "let body = item.body ?? item.data ?? item;"
    )
    definitive = nodes.get("Definitive Failure Check", {})
    definitive_parameters = definitive.get("parameters", {})
    definitive_parameters["jsCode"] = definitive_parameters.get("jsCode", "").replace(
        "item.body ?? error.response?.body ?? error.body",
        "item.body ?? item.data ?? error.response?.body ?? error.response?.data ?? error.body",
    )
    out.joinpath(source.name).write_text(json.dumps([envelope], indent=2) + "\n")

creds = [
    {"id": "T0w0H2O4M9E6W4T6", "name": "JP - FastAPI Internal API", "type": "httpHeaderAuth", "data": {"name": "x-api-key", "value": "fake-internal-key"}},
    {"id": "telegram_bot", "name": "Telegram Bot", "type": "telegramApi", "data": {"accessToken": "fake-token", "baseUrl": "http://telegram:8000"}},
]
out.joinpath("credentials.json").write_text(json.dumps(creds, indent=2) + "\n")
PY

"${compose[@]}" run --rm n8n import:credentials --input=/imports/credentials.json > "$LOG_DIR/import-credentials.log" 2>&1
"${compose[@]}" run --rm n8n import:workflow --input=/imports/JP___Search_Cycle.json > "$LOG_DIR/import-search.log" 2>&1
"${compose[@]}" run --rm n8n import:workflow --input=/imports/JP___Notifications.json > "$LOG_DIR/import-notifications.log" 2>&1
"${compose[@]}" run --rm n8n import:workflow --input=/imports/JP___Firecrawl_Discovery.json > "$LOG_DIR/import-firecrawl.log" 2>&1
"${compose[@]}" run --rm n8n import:workflow --input=/imports/JP___FastAPI_Health_Check.json > "$LOG_DIR/import-health.log" 2>&1
"${compose[@]}" run --rm n8n import:workflow --input=/imports/JP___Search_Plan.json > "$LOG_DIR/import-plan.log" 2>&1

"${compose[@]}" run --rm n8n list:workflow 2>&1 | tee "$LOG_DIR/registered-workflows.log"
"${compose[@]}" run --rm n8n execute --id=workflow-search-cycle --rawOutput 2>&1 | tee "$LOG_DIR/execute-search.json"
"${compose[@]}" run --rm n8n execute --id=workflow-notifications --rawOutput 2>&1 | tee "$LOG_DIR/execute-notifications.json"

# Re-run only the notification workflow against an ambiguous Telegram failure.
# The error output must be fenced: no acknowledge and no definitive fail call.
printf 'ambiguous\n' > "$LOG_DIR/telegram-mode"
"${compose[@]}" run --rm n8n execute --id=workflow-notifications --rawOutput 2>&1 | tee "$LOG_DIR/execute-notifications-ambiguous.json"

python3 - "$LOG_DIR" <<'PY'
import json, pathlib
log_dir = pathlib.Path(__import__("sys").argv[1])
events = [json.loads(line) for line in (log_dir / "stub-events.jsonl").read_text().splitlines()]
search = [e for e in events if e["kind"] == "search"]
select = [e for e in events if e["kind"] == "select-next"]
telegram = [e for e in events if e["kind"] == "telegram"]
ack = [e for e in events if e["kind"] == "acknowledge"]
fail = [e for e in events if e["kind"] == "fail"]
assert len(select) == 2, select
assert len(search) == 1, search
assert len(telegram) == 2, telegram
assert telegram[0]["body"]["chat_id"] == "chat-stub", telegram
assert sum(event["mode"] == "success" for event in telegram) == 1, telegram
assert sum(event["mode"] == "ambiguous" for event in telegram) == 1, telegram
assert len(ack) == 1, ack
assert sum(event["mode"] == "success" for event in ack) == 1, ack
assert sum(event["mode"] == "ambiguous" for event in ack) == 0, ack
assert len(fail) == 1, fail
assert sum(event["mode"] == "ambiguous" for event in fail) == 1, fail
assert fail[0]["body"]["definitive"] is False, fail
registered = sum(1 for line in (log_dir / "registered-workflows.log").read_text().splitlines() if "|" in line)
assert registered == 5, registered
print(json.dumps({"registered": registered, "select_next": len(select), "search": len(search), "telegram": len(telegram), "destination": telegram[0]["body"]["chat_id"], "success_acknowledgements": len(ack), "ambiguous_acknowledgements": 0, "ambiguous_non_definitive_failures": len(fail)}, indent=2))
PY
