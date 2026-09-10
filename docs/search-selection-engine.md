# Search Selection Engine (005.7)

## Ownership and boundary

FastAPI owns the authoritative taxonomy in `backend/app/domain/taxonomy.py`, candidate generation, deterministic ranking, freshness, execution correlation, and provider execution. PostgreSQL owns durable history and claim/state constraints. n8n only calls the two authenticated internal endpoints and never receives provider credentials.

Selection performs no provider request and no insert or update against `provider_usage`, `provider_state`, or `provider_minute_usage`. Its quota read is advisory. `run_ingestion()` remains the final authority and atomically reserves quota immediately before a provider request; it can reject an intent if capacity was consumed after selection.

## Candidates and ranking

A logical candidate is one authoritative role and location. Its stable ID is `role_id::location_id`; keywords and provider location are derived from those taxonomy records. The current taxonomy has 29 roles and 12 locations, so generation is a finite, ordered set of 348 candidates and does not call providers.

The v1 ranking is deterministic and lower is better:

```text
score = role/location priority * 100 + location tier * 10
score -= 1000 when the candidate has never succeeded
tie-break = candidate_id ascending
```

The candidate priority is currently the lower numeric value of the role and location priorities. This is a simple v1 policy, not ML, AI, adaptive learning, or provider routing.

## Freshness and history

Selection excludes a candidate selected in the last 15 minutes and one that succeeded in the last 24 hours. A selected claim older than 15 minutes is atomically changed to `failed` with reason `abandoned claim`; its original `selected_at` remains unchanged and `completed_at` records cleanup. Started executions are protected by the active-claim unique index and are terminated by the executor on every handled failure path; there is no selector cleanup of an execution whose provider request may still be running. A catastrophic failure after selected -> started may leave the execution in started. The current abandonment cleanup only reclaims stale selected claims. Recovery/reclamation of stale started executions is deferred to a future milestone.

History distinguishes selection, start, success, and failure. Metrics are nullable until execution succeeds and then come from the actual `IngestionResult`; selection never fabricates success.

## Correlation and state machine

`POST /ingestion/internal/select-next` returns at most one canonical intent and one database-generated `execution_id`. The same ID is included inside the intent. When `/ingestion/internal/search` receives an ID, the row must exist, be `selected`, and match the authoritative role, location, keywords, and priority represented by its `candidate_id`. Unknown IDs return 404; stale, terminal, or mismatched IDs return 409.

Allowed state transitions are compare-and-set updates:

```text
selected -> started
started  -> succeeded
started  -> failed
selected -> failed  (abandoned-claim cleanup only)
```

Every executor transition updates only a row in the expected prior state and requires one affected row. Replay or competing execution therefore fails rather than rebinding or corrupting history. Legacy 005.6 endpoints and direct `/internal/search` calls may omit `execution_id`; that compatibility path executes without selector history.

## PostgreSQL invariants

`chk_status_valid` permits only `selected`, `started`, `succeeded`, and `failed`. `uq_active_claim` is a partial unique index on `candidate_id` for both `selected` and `started`, preventing two active executions for one logical candidate. The schema uses timezone-aware timestamps and candidate/status lookup indexes.

## API and current scope

Both internal endpoints require `X-Api-Key`. Responses and logs do not include provider keys or authorization headers. The current selector/executor targets Adzuna only, matching the 005.6 milestone. Jooble remains available through its legacy authenticated endpoint. Weekly and monthly quota values are policy metadata and are not actively reserved in 005.6A.
