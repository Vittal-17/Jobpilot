# Automation Audit Report: JobPilot

## 1. Pipeline & Worker Architecture
* Table: `[Entry Point / Worker] | [File Path] | [Trigger Type] | [Target Operations]`

| Entry Point / Worker | File Path | Trigger Type | Target Operations |
|---|---|---|---|
| `select_next_search_endpoint` | `backend/app/api/endpoints/ingestion.py` | API / Orchestration | Selects candidate, checks limits, reserves execution claim |
| `internal_execute_search` | `backend/app/api/endpoints/ingestion.py` | API / Orchestration | Executes provider search, saves jobs, updates execution state |
| `ingest_adzuna` | `backend/app/api/endpoints/ingestion.py` | API Trigger | Manual Adzuna job ingestion (Legacy) |
| `ingest_jooble` | `backend/app/api/endpoints/ingestion.py` | API Trigger | Manual Jooble job ingestion (Legacy) |
| `JP - Search Cycle Orchestration` | `n8n / Orchestration` | Webhook / Timer | Orchestrates select-next and execute loop |

## 2. Critical Bugs & Runtime Failure Vectors
* Table: `[File Path] | [Line Number] | [Failure Mode / Race Condition] | [Fix]`

| File Path | Line Number | Failure Mode / Race Condition | Fix |
|---|---|---|---|
| `scripts/deploy.py` | 367 | Command-line injection/leak: `PGPASSWORD=verify` passed as a CLI flag (`-e`) makes the password visible in process tables (`ps`). | Pass credentials via environment variables (`env=run_env`) without adding them to the CLI arguments. |
| `scripts/deploy.py` | 380 | Command-line injection/leak: `PGPASSWORD=verify` passed as a CLI flag (`-e`) makes the password visible in process tables (`ps`). | Pass credentials via environment variables (`env=run_env`) without adding them to the CLI arguments. |
| `backend/app/api/endpoints/ingestion.py` | 149 | Information leak: `traceback.print_exc()` dumps raw stack traces to stdout, risking leakage of provider configs or internal states to logs. | Use standard `logging.exception()` and ensure sensitive variables are sanitized. |
| `backend/app/services/search_selector.py` | 92 | Transaction isolation violation: `db.commit()` inside nested transaction commits the entire outer session state unexpectedly. | Rely on the caller to commit or use an autonomous session/transaction specifically for cleanup. |
| `backend/app/api/endpoints/ingestion.py` | 179 | Transaction isolation violation: `db.commit()` inside `_best_effort_close_routing_claim` can inadvertently commit other pending operations. | Use a separate DB session or ensure no other pending operations exist before calling. |

## 3. Resilience & External Integration Flaws
* **Retry Failures:** Neither `AdzunaProvider` nor `JoobleProvider` implement retry mechanisms or exponential backoff for network timeouts or transient 5xx errors from the provider APIs. Failures immediately bubble up and consume the quota without recovery.
* **Rate-Limiting Vulnerabilities:** While internal rate limits are enforced, there is no dynamic handling of HTTP 429 Too Many Requests responses from upstream providers (e.g., respecting `Retry-After` headers).
* **Error Bubbling:** External provider exceptions (`httpx.RequestError`, `httpx.TimeoutException`) bubble up to the FastAPI routes and return 500/502s directly to the caller, requiring the orchestration layer to handle all retry logic.
* **Synchronous I/O Blocking:** Provider requests use synchronous `httpx.Client()`. In a high-concurrency scenario, this blocks FastAPI's worker threads, potentially leading to thread pool exhaustion and denial of service.

## 4. Remediation Checklist
* **P0:**
  * [ ] Remove `-e PGPASSWORD=verify` from `docker exec` CLI arguments in `scripts/deploy.py` to prevent credential leakage.
  * [ ] Remove `traceback.print_exc()` from `backend/app/api/endpoints/ingestion.py` to secure logging.
  * [ ] Refactor `db.commit()` calls in `_clean_abandoned_claims` and `_best_effort_close_routing_claim` to prevent transaction scope violations.
* **P1:**
  * [ ] Implement exponential backoff and retry logic for `httpx` calls in provider implementations.
  * [ ] Migrate synchronous HTTP requests (`httpx.Client`) and endpoints (`def`) to asynchronous (`httpx.AsyncClient` and `async def`) to prevent thread blocking.
  * [ ] Add handling for upstream HTTP 429 `Retry-After` rate-limiting.
* **P2:**
  * [ ] Remove orphaned manual endpoints (`ingest_adzuna`, `ingest_jooble`) from `backend/app/api/endpoints/ingestion.py`.
  * [ ] Add type annotations to `scripts/deploy.py`.
  * [ ] Add dead-letter queue or persistent logging for jobs failing validation during ingestion.
