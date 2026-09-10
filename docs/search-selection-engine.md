# Search Selection and Provider Routing (005.7 / 005.7B)

## Ownership and boundary

FastAPI owns the authoritative taxonomy, candidate selection, provider routing, execution correlation, and provider execution. PostgreSQL owns durable history, quota usage, and claim/state constraints. n8n only calls the two authenticated internal endpoints and never receives provider credentials.

Selection performs no provider request and no insert or update against `provider_usage`, `provider_state`, or `provider_minute_usage`. Its quota read is advisory. `run_ingestion()` remains the final authority and atomically reserves quota immediately before a provider request; it can reject an intent if capacity was consumed after selection.

## Provider routing (005.7B)

After 005.7 claims one candidate, FastAPI routes that claim to exactly one enabled, validly configured provider. The v1 order is explicit provider priority (`adzuna`, then `jooble`), constrained remaining minute/daily/lifetime capacity descending, then canonical provider name ascending. Priority is authoritative; capacity and name make ranking explicit within equal-priority providers. Routing calls each provider's local `validate_config()` but performs no HTTP request.

The decision is persisted in the existing nullable `search_execution.provider_name` while status remains `selected`. `select-next` returns the same provider both at the response top level and inside the intent. n8n forwards the intent unchanged. The executor verifies that the requested provider equals the persisted decision and transitions to `started` only with that provider; it never accepts an arbitrary override and never falls back to another provider.

Routing reads the same effective minute, daily, and lifetime limits used by 005.6 (`min(provider ceiling, account ceiling, safety budget)`) and current PostgreSQL usage. It never writes quota. A concurrent worker may consume the observed capacity before execution; this expected race produces a clean 429 from the authoritative atomic reservation and a failed execution, not rerouting or a second provider call.

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

`POST /ingestion/internal/select-next` returns at most one canonical intent, one database-generated `execution_id`, and one provider decision. The same ID and provider are included inside the intent. When `/ingestion/internal/search` receives an ID, the row must exist, be `selected`, match the authoritative candidate, and already be routed to the supplied provider. Unknown IDs return 404; stale, terminal, candidate-mismatched, or provider-mismatched IDs return 409.

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

Both internal endpoints require `X-Api-Key`. Responses and logs do not include provider keys or authorization headers. Both Adzuna and Jooble are eligible for the current canonical intent. Weekly and monthly quota values remain policy metadata and are not actively reserved in 005.6A or ranked in 005.7B.
