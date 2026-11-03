#!/usr/bin/env python3
"""Offline FastAPI and Telegram stand-in used by the pinned n8n harness."""
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = os.environ.get("STUB_LOG", "/logs/stub-events.jsonl")
TELEGRAM_MODE = os.environ.get("STUB_TELEGRAM_MODE", "success")
state = {"select": 0, "search": 0, "telegram": 0}

def current_telegram_mode():
    mode_file = Path("/logs/telegram-mode")
    return mode_file.read_text(encoding="utf-8").strip() if mode_file.exists() else TELEGRAM_MODE

def record(kind, body):
    event = {"kind": kind, "body": body}
    if kind in {"telegram", "acknowledge", "fail"}:
        event["mode"] = current_telegram_mode()
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, sort_keys=True) + "\n")

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def reply(self, payload, status=200):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            body = raw.decode("utf-8", errors="replace")
        if self.path.endswith("/select-next"):
            state["select"] += 1
            record("select-next", body)
            if state["select"] == 1:
                self.reply({"action": "execute", "intent": {"role_id": "ROLE-TEST", "location": "Offline"}})
            else:
                self.reply({"action": "stop", "reason": "stub_exhausted"})
            return
        if self.path.endswith("/search"):
            state["search"] += 1
            record("search", body)
            self.reply({"status": "ok", "provider": "stub", "fetched": 0})
            return
        if self.path.endswith("/notifications/recover-stale"):
            record("recover-stale", body)
            self.reply({"recovered": 0})
            return
        if self.path.endswith("/notifications/claim"):
            record("claim", body)
            self.reply({"user_id": 42, "delivery_id": "delivery-stub", "telegram_chat_id": "chat-stub", "recommendations": [{"url": "https://offline.invalid/job", "title": "Offline job", "company": "Stub Co", "location": "Offline"}]})
            return
        if self.path.endswith("/notifications/send-start"):
            record("send-start", body)
            self.reply({"authorized": True})
            return
        if self.path.endswith("/notifications/acknowledge"):
            record("acknowledge", body)
            self.reply({"acknowledged": True})
            return
        if self.path.endswith("/notifications/fail"):
            record("fail", body)
            self.reply({"failed": True})
            return
        if "/botfake-token/" in self.path:
            state["telegram"] += 1
            mode = current_telegram_mode()
            event = {"kind": "telegram", "body": body, "mode": mode, "status": 500 if mode == "ambiguous" else 200}
            os.makedirs(os.path.dirname(LOG), exist_ok=True)
            with open(LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, sort_keys=True) + "\n")
            if mode == "ambiguous":
                self.reply({"ok": False, "description": "offline gateway timeout"}, 500)
                return
            self.reply({"ok": True, "result": {"message_id": 1}})
            return
        self.reply({"error": "not found"}, 404)

if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
