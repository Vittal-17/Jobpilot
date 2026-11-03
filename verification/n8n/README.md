# Offline n8n fragment/runtime harness

`run_harness.sh` uses only the pinned `docker.n8n.io/n8nio/n8n:2.38.1` image and
two in-network Python stub aliases (`fastapi` and `telegram`). It imports the
repository JSON fragments into an isolated SQLite state directory, registers
all five workflows, runs the search-cycle and notification workflows, and
asserts the select/search loop plus the Telegram destination. It then repeats
notification delivery with an ambiguous Telegram failure and asserts that
acknowledgement and definitive-failure calls are fenced. No external
network is available to the n8n container's application calls.

Run from this directory:

```sh
./run_harness.sh
```

Evidence is retained in `logs/`. Each run creates disposable transformed imports
and n8n SQLite state under a temporary directory, then removes that runtime on
exit; tracked files under `imports/` are never rewritten.
The stub intentionally returns one `execute` then one `stop`, and returns a
single authorized notification with a fake chat ID. It never sends externally.
