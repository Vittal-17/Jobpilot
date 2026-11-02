#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
export COMPOSE_PROJECT_NAME=jobpilot-n8n-harness
ROOT="$(cd ../.. && pwd)"
LOG_DIR="${N8N_LOGS_DIR:-$SCRIPT_DIR/logs}"
RUNTIME_DIR="$(mktemp -d "${TMPDIR:-/tmp}/jobpilot-n8n-baseline.XXXXXX")"
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
rm -f "$LOG_DIR"/baseline-*.log "$LOG_DIR"/baseline-*.json "$LOG_DIR"/telegram-mode
: > "$LOG_DIR/stub-events.jsonl"

# Build only a disposable baseline workflow.  The tracked verification imports
# are never used as scratch space and are not rewritten by this reproduction.
python3 - "$ROOT" "$IMPORT_DIR" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
source = root / "backend" / "JP___Search_Cycle.json"
data = json.loads(source.read_text(encoding="utf-8"))
workflow = data[0] if isinstance(data, list) and data and "nodes" in data[0] else {
    "name": source.stem,
    "active": False,
    "nodes": data,
    "connections": {},
}
workflow = dict(workflow)
workflow["id"] = "workflow-search-cycle-baseline"
workflow["active"] = False
for node in workflow.get("nodes", []):
    if node.get("type") == "n8n-nodes-base.scheduleTrigger":
        node["type"] = "n8n-nodes-base.manualTrigger"
    if node.get("name") == "Loop":
        node["name"] = "Bounded Server Loop"
        node["id"] = "node-loop-baseline"
        node["parameters"] = {"jsCode": "const state = $input.first().json || {}; if (state.continue !== true || state.iterations >= (state.max_iterations || 20)) return [{json: {...state, action: 'stop'}}]; return [{json: state}];"}
connections = workflow["connections"]
connections["Select Next"]["main"][0][0]["node"] = "Bounded Server Loop"
connections["Bounded Server Loop"] = connections.pop("Loop")
connections["Bounded Server Loop"]["main"][0][0]["node"] = "Action Check"
out.joinpath("JP___Search_Cycle_baseline.json").write_text(json.dumps([workflow], indent=2) + "\n")
credentials = [
    {"id": "T0w0H2O4M9E6W4T6", "name": "JP - FastAPI Internal API", "type": "httpHeaderAuth", "data": {"name": "x-api-key", "value": "fake-internal-key"}},
]
out.joinpath("credentials.json").write_text(json.dumps(credentials, indent=2) + "\n")
PY

"${compose[@]}" run --rm n8n import:credentials --input=/imports/credentials.json > "$LOG_DIR/baseline-import-credentials.log" 2>&1
before=$(wc -l < "$LOG_DIR/stub-events.jsonl")
"${compose[@]}" run --rm n8n import:workflow --input=/imports/JP___Search_Cycle_baseline.json > "$LOG_DIR/baseline-import-workflow.log" 2>&1
"${compose[@]}" run --rm n8n execute --id=workflow-search-cycle-baseline --rawOutput | tee "$LOG_DIR/execute-baseline.json"
python3 - "$before" "$LOG_DIR" <<'PY'
import json
import pathlib
import sys

log_dir = pathlib.Path(sys.argv[2])
events = [json.loads(x) for x in (log_dir / "stub-events.jsonl").read_text().splitlines()[int(sys.argv[1]):]]
select = [x for x in events if x["kind"] == "select-next"]
search = [x for x in events if x["kind"] == "search"]
assert len(select) == 1, select
assert len(search) == 0, search
print("baseline defect reproduced: select-next=1, search=0")
PY
