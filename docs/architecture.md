# Architecture

## Domain Concepts

### Canonical Job Model

The `Job` model is the central domain concept representing a normalized job opportunity.
Regardless of where a job listing is discovered, it is converted into this canonical format before further processing.

Data flow:
```text
FastAPI
   ↓
Pydantic domain model (app.models.job.Job)
   ↓
SQLAlchemy persistence model (app.db.models.job.JobModel)
   ↓
PostgreSQL
```

### Persistence and Domain Separation

The Pydantic domain model and SQLAlchemy persistence model are intentionally kept separate:
1. **Clean Architecture**: The domain layer (Pydantic) has no dependency on the database layer (SQLAlchemy), making it easier to test and reason about.
2. **Validation Rules**: Pydantic handles parsing, structural validation, and ensuring business rules at the application boundary, whereas SQLAlchemy enforces strict database-level constraints (uniqueness, foreign keys, not-null).
3. **Flexibility**: We can change the database schema or ORM without breaking the core domain logic, and vice versa.

### Data Integrity

- **Uniqueness Strategy**: A job is uniquely identified by the tuple `(source, source_job_id)`. We do not assume `source_job_id` is globally unique across different providers, so the database enforces a `UniqueConstraint` on both columns.
- **Constraints**: Database-level `CheckConstraint`s ensure `match_score` is strictly between `0-100` and `salary_min`/`salary_max` are non-negative.

### Multi-Provider Ingestion
The system uses a `JobProvider` interface to normalize external sources (e.g., Adzuna, Jooble) into the canonical `Job` model.
```text
JobSearchQuery
      ↓
Provider Interface
   /     \
Adzuna  Jooble
   \     /
Canonical Job
      ↓
Ingestion Service
      ↓
Job Repository
      ↓
PostgreSQL
```
- **Stable Source IDs**: We extract the actual provider job ID (e.g., Jooble ID, Adzuna ID) as `source_job_id`. This works seamlessly with the `(source, source_job_id)` idempotency constraint in the database, allowing us to safely retry or re-ingest pages without generating duplicate rows.
- **Bangalore-First Query Capability**: The shared `JobSearchQuery` supports a `location` parameter. Providers map this to their respective APIs. Adzuna yields precise "Bangalore, Karnataka" locations, while Jooble returns broader "India" results, which are subsequently scored and filtered for local relevance.
- **Provider Integration (Jooble India)**: Jooble ingestion connects directly to the Indian regional endpoint `https://in.jooble.org/api/{api_key}` using `JOOBLE_IN_API_KEY`. API keys are region-specific to `in.jooble.org`, and request URLs are scrubbed in logs to protect credentials.
- **Defensive Ingestion**: The ingestion service isolates provider errors. Connection errors, timeouts, or non-200 responses fail closed and record execution telemetry without infinite retry loops.

### Two-Tier Deduplication
JobPilot implements a two-tier deduplication architecture in [app/db/repository/job_repository.py](file:///home/vittal/Projects/jobpilot/Jobpilot/backend/app/db/repository/job_repository.py):
1. **Source-Level Idempotency**: Strict unique constraint on `(source, source_job_id)` in the `job_sources` table. Repeated API pulls of the same provider posting are safely ignored.
2. **Cross-Provider Canonical Deduplication**: A deterministic SHA-256 hash (`canonical_hash`) is maintained on the `jobs` table:
   - Evaluates normalized job URLs (stripping query parameters like `utm_*` and ignoring aggregator domains).
   - Falls back to normalized text tokens: `ETL|{company}|{title}|{location}`.
   - When a match is detected, the secondary source is linked in `job_sources` while preserving the existing canonical `jobs` row, preventing duplicate listings across providers.

### Asynchronous Enrichment & Scraper Fallback
Listings ingested with truncated snippet descriptions are enriched asynchronously by the `enrichment_worker` CLI process:
- **Queue & Leases**: Rows in `job_enrichments` are claimed in batches using `SELECT ... FOR UPDATE SKIP LOCKED` with explicit expiration lease tokens.
- **SSRF Defense**: The native scraping client validates destination IPs against loopback, private subnets (RFC 1918), link-local, and cloud metadata IPs (`169.254.169.254`).
- **Firecrawl Scraper Fallback**: If native fetching encounters non-terminal errors (HTTP 403, 401, 429) or extraction fails, the worker attempts Firecrawl fallback (`FirecrawlClient`), governed by monthly credit reservation in `provider_usage` (`firecrawl_monthly`).
- **Content Validation**: Scraped markdown is validated to reject anti-bot challenges ("checking your browser", "enable cookies"), redirect/interstitial wrappers ("you are being redirected", "view ad here"), and undersized responses (<200 chars).
- **Graceful Fallback**: On SSRF violations, terminal 404/410 errors, budget exhaustion, or content rejection, the worker calls `complete_with_snippet` to evaluate eligibility on the snippet rather than abandoning the listing.

### Deterministic Fresher Eligibility & Match Scoring
- **Fresher Eligibility Gate** ([app/services/eligibility.py](file:///home/vittal/Projects/jobpilot/Jobpilot/backend/app/services/eligibility.py)): Evaluates postings against strict criteria: rejects explicit seniority titles, authoritatively rejects requirements $>1$ year of experience, disambiguates company tenure and business longevity from applicant requirements, and tightly scopes graduate/trainee roles away from administrative or recruiter positions.
- **Match Scoring Engine** ([app/services/matching_service.py](file:///home/vittal/Projects/jobpilot/Jobpilot/backend/app/services/matching_service.py)): Evaluates eligible jobs against operator preferences across title, technical skills, location, remote preference, and experience heuristic, normalized to a $0\text{--}100$ score. Matches scoring $\ge 50$ persist to `recommendation_history`.

### Platform Surfaces & Edge Routing
- **Edge Reverse Proxy**: Caddy 2.8 negotiates ACME TLS, enforces security headers, and routes `/api/*` to FastAPI, `/` to the React SPA frontend, and `n8n.{$DOMAIN}` through the Auth Gateway.
- **Auth Gateway**: Microservice providing forward authentication (`forward_auth`) for n8n with IP-based failed login rate limiting and HMAC-SHA256 session cookies.
- **Frontend SPA**: React 19 / Vite application providing public observability (Today feed, Jobs archive, detail views, System status) and authenticated operator controls.
