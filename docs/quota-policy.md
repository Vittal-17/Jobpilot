# JobPilot Quota Policy (005.6A)

JobPilot explicitly separates provider limitations, account limitations, and its own internal safety budgets to guarantee safe execution and preserve provider standing.

## Conceptual Layers

1. **PROVIDER CEILING**: The absolute limit officially documented by the provider.
2. **ACCOUNT CEILING**: An optional configurable limit representing account-specific restrictions or observed limits.
3. **JOBPILOT SAFETY BUDGET**: An intentional internal limit chosen by JobPilot to avoid aggressively consuming provider capacity.
4. **EFFECTIVE USABLE BUDGET**: The tightest applicable constraint across all layers (`min(provider, account, safety)`), evaluated per dimension.

## Current Policy Configuration

### Adzuna
- **Provider Ceiling**: 25/minute, 250/day, 1000/week, 2500/month (per official docs).
- **Account Ceiling**: None (Unspecified).
- **JobPilot Safety Budget**: 25/day (Internal policy to stay conservative).
- *Note*: Historically, JobPilot assumed a `10/day` Adzuna limit. This was an internal safety budget, **not** a provider fact.

### Jooble
- **Provider Ceiling**: 500 lifetime (per official REST API docs). Jooble does NOT document a 1/day ceiling.
- **Account Ceiling**: None (Unspecified).
- **JobPilot Safety Budget**: 2/day, 500 lifetime.
- *Note*: Historically, JobPilot assumed a `1/day` Jooble limit. This was an internal safety budget, **not** a provider fact.

### Firecrawl (Scraper Fallback)
- **Service Ceiling**: Monthly credit allocation based on plan (default budget: 1000 requests/month).
- **Enforcement Window**: Monthly calendar window bucketed by `DATE_TRUNC('month', CURRENT_DATE)::DATE`.
- **Exhaustion Behavior**: On HTTP 402 ("Payment Required"), JobPilot immediately synchronizes the recorded monthly usage to the configured ceiling via `_sync_firecrawl_budget`, falling back cleanly to snippet evaluation for subsequent jobs.

## Architecture & Concurrency

- Quotas are enforced **atomically** in PostgreSQL via `provider_usage`, `provider_state`, and `provider_minute_usage` tables.
- **Enforcement Scope**: PostgreSQL reservation accounting (`acquire_provider_request_slot`) actively enforces all five quota dimensions: `minute`, `daily`, `weekly`, `monthly`, and `lifetime`.
- **Multi-Day Window Aggregation**:
  - `weekly` limits aggregate `SUM(request_count)` in `provider_usage` across `DATE_TRUNC('week', CAST(:d AS date))` through `DATE_TRUNC('week', CAST(:d AS date)) + INTERVAL '7 days'`.
  - `monthly` limits aggregate `SUM(request_count)` in `provider_usage` across `DATE_TRUNC('month', CAST(:d AS date))` through `DATE_TRUNC('month', CAST(:d AS date)) + INTERVAL '1 month'`.
- **Advisory Serialization**: When evaluating multi-day windows (`weekly` or `monthly`), the reservation transaction acquires a PostgreSQL advisory transaction lock (`pg_advisory_xact_lock(hashtext('provider_quota'), hashtext(:provider_name))`). This serializes concurrent requests for the same provider and eliminates race conditions across distributed date rows.
- **Provider Routing Integration**: `get_provider_capacity` in [app/services/provider_router.py](file:///home/vittal/Projects/jobpilot/Jobpilot/backend/app/services/provider_router.py) queries active usage across `minute`, `daily`, `weekly`, `monthly`, and `lifetime` windows. A provider is marked unavailable if any window reaches zero capacity.
- **Preemptive Commit**: Quota reservations occur transactional before external HTTP requests are issued. If any quota limit is exceeded, the transaction rolls back and the request is blocked. If an external provider call fails after reservation, the slot remains consumed to fail closed against upstream provider drain.
