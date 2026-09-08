# JobPilot

JobPilot is a production-minded backend engine designed to ingest, normalize, and score job listings from multiple global providers. It serves as the foundation for a highly reliable, automated job search orchestration system.

## Features

- **Multi-Provider Ingestion:** Normalizes Adzuna and Jooble job listings into a single canonical domain model.
- **Strict Quota Controls:** Built-in atomic PostgreSQL-backed daily and lifetime quota accounting to prevent unexpected provider billing or rate-limiting.
- **Provider-Level Idempotency:** Implements strict source-level deduplication via database unique constraints on `(source, source_job_id)`, preventing duplicate inserts during retries. **Note: Semantic cross-provider deduplication (identifying the same job across different providers) is NOT yet implemented.**
- **FastAPI Foundation:** Clean, type-safe API for triggering manual ingestion runs.

## Architecture

The intended production architecture is:

```
Internet
    ↓
Reverse Proxy / HTTPS
    ↓
n8n (Future Orchestration Layer)
    ↓
PRIVATE DOCKER NETWORK
    ↓
FastAPI (Business Logic / Ingestion Endpoints)
    ↓
PostgreSQL (State / Persistence / Quotas)
```

## Current Providers & Quota Policy

- **Adzuna India:** 10 requests / day limit.
- **Jooble:** 1 request / day limit, 500 requests lifetime limit.

## Local Setup

1. Copy `.env.example` to `.env` and fill in secrets.
2. Start PostgreSQL via `docker-compose up -d db`.
3. Apply migrations: `alembic upgrade head`.
4. Run the API: `fastapi dev app/main.py`.

## Testing

JobPilot uses `pytest` and `respx` for robust isolation.
To run tests:
```bash
export PYTHONPATH=backend
pytest tests/
```
**Safety Note:** Tests strictly require the `TEST_DATABASE_URL` environment variable to be set. The URL must be explicitly verified and the database name must contain `_test`, or else the test suite will instantly abort to protect development data.

## Security Notes

- The database is only exposed to `127.0.0.1` locally in Docker Compose.
- Provider secrets are aggressively stripped from error traces and exception messages.
- Endpoints require `X-Api-Key` authentication via the `API_SECRET_KEY` environment variable. The production API secret must be explicitly configured, non-placeholder, and >=16 chars; high-entropy random generation is highly recommended.

## Limitations & Roadmap

- **Scoring:** Match scoring is planned but not fully implemented.
- **Orchestration:** Currently waiting to be connected to n8n for fully scheduled automated runs.

*(Note: n8n orchestration and production deployment are planned for future phases and do not exist yet.)*

### Quota Consumption Semantics
- **Atomic Pre-emption:** JobPilot explicitly acquires and commits the provider quota *before* issuing any external network request.
- **Fail-Safe:** If an external network failure occurs, the quota remains consumed to strictly prevent unbounded retry loops from draining upstream provider credits during a sustained provider outage.
- **Authentication:** Ingestion endpoints require an internal orchestration API key to prevent arbitrary internet users from exhausting limits.
