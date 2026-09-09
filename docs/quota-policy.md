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

## Architecture & Concurrency

- Quotas are enforced **atomically** in PostgreSQL via `provider_usage`, `provider_state`, and `provider_minute_usage` tables.
- **Enforcement Scope**: PostgreSQL reservation accounting actively enforces `minute`, `daily`, and `lifetime` limits. `weekly` and `monthly` provider ceilings are currently represented strictly as policy metadata (for 005.7+ planning) but are **not** yet independently enforced. This is intentional scope for 005.6A because the JobPilot safety budgets keep those provider ceilings unreachable under normal conditions.
- Reservations occur **preemptively**, using transactional SQL upserts (`ON CONFLICT DO UPDATE`) before making external HTTP requests.
- If a reservation fails (limit exceeded), the transaction rolls back, and no provider request is made.
- The implementation is designed to prevent concurrent oversubscription of the actively enforced quota dimensions.
