# JobPilot

> Autonomous job intelligence engine and operator platform tailored for software engineering roles in India (Bangalore / Remote).

JobPilot is designed to discover, normalize, deduplicate, enrich, and score job listings from multiple upstream providers. Built as a single-operator platform with public-first observability, JobPilot serves an unauthenticated public feed and telemetry dashboard while providing private, authenticated controls and scheduled automation for the operator.

---

## Table of Contents

- [Overview & Operating Model](#overview--operating-model)
- [Architecture & Topology](#architecture--topology)
- [Component Stack](#component-stack)
- [Access Model & Surfaces](#access-model--surfaces)
- [Data Pipeline & Lifecycle](#data-pipeline--lifecycle)
  - [1. Search Planning & Candidate Selection](#1-search-planning--candidate-selection)
  - [2. Provider Routing & Quota Accounting](#2-provider-routing--quota-accounting)
  - [3. Normalization & Ingestion](#3-normalization--ingestion)
  - [4. Two-Tier Deduplication](#4-two-tier-deduplication)
  - [5. Fresher Eligibility Gate](#5-fresher-eligibility-gate)
  - [6. Asynchronous Enrichment & SSRF Defense](#6-asynchronous-enrichment--ssrf-defense)
  - [7. Deterministic Match Scoring](#7-deterministic-match-scoring)
  - [8. Notification Orchestration](#8-notification-orchestration)
- [Providers & Quota Policies](#providers--quota-policies)
- [Deployment & Safety Engine](#deployment--safety-engine)
- [CI/CD Supply Chain](#cicd-supply-chain)
- [Security Architecture](#security-architecture)
- [Local Development](#local-development)
- [Testing & Quality Gates](#testing--quality-gates)
- [Configuration Reference](#configuration-reference)
- [Operational Status & Limitations](#operational-status--limitations)

---

## Overview & Operating Model

JobPilot is engineered to solve the signal-to-noise problem in technical job discovery without exhausting provider rate limits or creating duplicate entries.

Key product principles:
- **Public-First Observability**: The primary dashboard ("Today"), live normalized job archive ("Jobs"), detail view, and pipeline telemetry ("System") are publicly accessible without authentication.
- **Single-Operator Portfolio Model**: The platform is intentionally architected for a single operator (canonical `user_id = 1`). User management, saved jobs, application tracking, search vectors, profile preferences, and workflow orchestration are protected operator surfaces.
- **Server-Authoritative State Machine**: All business logic, candidate selection, quota accounting, and matching rules reside strictly within FastAPI and PostgreSQL. External schedulers act solely as task runners.

---

## Architecture & Topology

The production topology separates edge routing, presentation, API state, background workers, and automation across isolated Docker bridge networks:

```
                                 INTERNET
                                    │
                    ┌───────────────┴───────────────┐
                    │     Caddy 2.8 Reverse Proxy   │ (Ports 80/443, ACME TLS)
                    └───────┬───────────────┬───────┘
                            │               │
     Host: {$DOMAIN}        │               │ Host: n8n.{$DOMAIN}
   ┌────────────────────────┴─┐           ┌─┴────────────────────────┐
   │                          │           │ Caddy forward_auth       │
   │ Path: /api/*             │ Fallback  │ URI: /verify             │
   │ (handle_path strips /api)│           │ Copy-Header: X-User      │
   ▼                          ▼           └────────────┬─────────────┘
┌──────────────┐     ┌──────────────┐                  │ Session Cookie
│   FastAPI    │     │ Frontend SPA │                  ▼ Verified
│  Container   │     │  (Nginx 80)  │        ┌──────────────────┐
│  Port: 8000  │     └──────────────┘        │   Auth Gateway   │ (FastAPI, Port 8080)
└──────┬───────┘                             │   Microservice   │ Handles /login, /logout, /verify
       │                                     └─────────┬────────┘
       │                                               │ Authorized Proxy
       │                                               ▼
       │                                     ┌──────────────────┐
       │                                     │   n8n Service    │ (Port 5678)
       │                                     │   Orchestrator   │ Inactive Workflow Templates
       │                                     └─────────┬────────┘
       │                                               │
       ├───────────────────────────────────────────────┤
       │           Internal Docker Networks            │
       ▼                                               ▼
┌───────────────────────────────────────────────────────────────┐
│                    PostgreSQL 16 Alpine                       │
│  - jobpilot (Application state, quotas, jobs, recommendations)│
│  - n8n_data (Dedicated isolated database for n8n workflows)   │
└──────────────────────────────┬────────────────────────────────┘
                               │
                               │ Internal Network: jobpilot_enrichment_backend
                               ▼
               ┌───────────────────────────────┐
               │       Enrichment Worker       │ (CLI process)
               │ - Row-level leased claims     │
               │ - SSRF + Firecrawl fallback   │
               │ - Match score persistence     │
               └───────────────────────────────┘
```

### Network Isolation Boundaries

| Network | Member Services | Purpose |
| :--- | :--- | :--- |
| `jobpilot_frontend` | `caddy`, `frontend`, `fastapi`, `auth-gateway`, `n8n` | Edge ingress and HTTP presentation routing. |
| `jobpilot_backend` | `fastapi`, `db`, `db-setup`, `n8n` | Authoritative application state and n8n database access. |
| `jobpilot_enrichment_backend` | `db`, `enrichment_worker` | Isolated database communication for background scraping and enrichment. |

---

## Component Stack

| Layer | Technologies | Primary Roles |
| :--- | :--- | :--- |
| **Edge Proxy** | [Caddy 2.8](file:///home/vittal/Projects/jobpilot/Jobpilot/Caddyfile) Alpine | Automated TLS (ACME), security headers (HSTS, nosniff, SAMEORIGIN), forward authentication, path stripping. |
| **Frontend** | [React 19](file:///home/vittal/Projects/jobpilot/Jobpilot/frontend/src/App.tsx), TypeScript, Vite 8, Tailwind CSS v4 | Responsive operator UI, TanStack Query v5 state cache, GSAP 3.15 + Lenis motion, Radix UI primitives. |
| **Auth Gateway** | Python 3.12, FastAPI, Jinja2, [auth-gateway/main.py](file:///home/vittal/Projects/jobpilot/Jobpilot/auth-gateway/main.py) | Caddy `forward_auth` endpoint, HMAC-SHA256 session cookies, bcrypt password verification, failed-attempt rate limiting per IP on login. |
| **Core API** | Python 3.12, [FastAPI](file:///home/vittal/Projects/jobpilot/Jobpilot/backend/app/main.py), SQLAlchemy 2.0, Pydantic v2 | Canonical domain models, quota enforcement, candidate selection, matching, internal orchestration endpoints. |
| **Database** | PostgreSQL 16 Alpine, [Alembic](file:///home/vittal/Projects/jobpilot/Jobpilot/backend/alembic.ini) | Multi-database isolation (`jobpilot`, `n8n_data`), atomic constraints, row-level locks (`SKIP LOCKED`). |
| **Enrichment** | Python 3.12 CLI ([app/services/enrichment_worker.py](file:///home/vittal/Projects/jobpilot/Jobpilot/backend/app/services/enrichment_worker.py)) | Asynchronous scraping worker, lease token handling, SSRF IP filtering, Firecrawl scraping fallback with monthly credit quotas, full-text parsing, score persistence. |
| **Automation** | [n8n 2.38.1](file:///home/vittal/Projects/jobpilot/Jobpilot/docker-compose.production.yml) | External scheduler hosting workflow definitions for search cycle loops and Telegram notification delivery (shipped inactive; require runtime activation). |

---

## Access Model & Surfaces

JobPilot enforces a three-tier access model:

| Surface Tier | Authentication Mechanism | Target Endpoints / Routes | Description |
| :--- | :--- | :--- | :--- |
| **Public Read** | None (Unauthenticated) | `/`<br>`/jobs`<br>`/jobs/:id`<br>`/system`<br>`GET /v1/jobs`<br>`GET /v1/jobs/{id}`<br>`GET /v1/recommendations`<br>`GET /v1/system/status`<br>`GET /health`<br>`GET /health/ready` | Read-only access to Today's recommendations, job archive, detail pages, pipeline telemetry, and health checks. |
| **Operator Admin** | Session Cookie (`session_token`) | `/signin`<br>`/saved`<br>`/applications`<br>`/search`<br>`/settings`<br>`POST /v1/auth/login`<br>`POST /v1/auth/logout`<br>`GET /v1/me`<br>`/v1/profile`<br>`/v1/saved`<br>`/v1/applications`<br>`/v1/searches` | Operator actions: saving listings, tracking applications, configuring search vectors, updating profile preferences. |
| **Edge Gateway** | Session Cookie (`jobpilot_admin_session`) | `https://n8n.{$DOMAIN}`<br>`/login`<br>`/logout`<br>`/verify` | Caddy `forward_auth` gate for the n8n orchestrator UI. Protected against failed login brute force (5 failures per 60s per IP), bcrypt-verified, open-redirect protected. |
| **Internal Orchestration** | Header: `X-Api-Key` (`API_SECRET_KEY`) | `POST /ingestion/adzuna`<br>`POST /ingestion/jooble`<br>`POST /ingestion/internal/select-next`<br>`POST /ingestion/internal/search`<br>`POST /ingestion/internal/notifications/*`<br>`GET /internal/health` | Machine-to-machine orchestration endpoints used by n8n. Evaluated via constant-time string comparison. |

---

## Data Pipeline & Lifecycle

### 1. Search Planning & Candidate Selection
The search selection engine ([app/services/search_selector.py](file:///home/vittal/Projects/jobpilot/Jobpilot/backend/app/services/search_selector.py)) deterministically decides which search variant to run next. It reconciles:
- Static domain taxonomy defined in [app/domain/taxonomy.py](file:///home/vittal/Projects/jobpilot/Jobpilot/backend/app/domain/taxonomy.py) (canonical engineering roles and locations).
- Dynamic operator-configured search vectors stored in the `user_searches` table.
- Active user demand prioritization: boosts candidates matching active operator search queries and profile preferred roles and locations.
- Variant quality telemetry: tracks outcomes per variant (`UNTRIED`, `UNKNOWN` 1-day retry cooldown, `NO_INVENTORY` 7-day penalty, `NO_FRESHER` 7-day penalty, and `PRODUCTIVE` with Beta-smoothed utility exploitation).
- Adaptive retrieval scope: fail-closed drought detection broadens granular Bangalore locations to canonical `"Bengaluru"` only when all variants are verified `NO_INVENTORY`.
- Deterministic fallback: rotates through penalized variants oldest-attempt-first (`min(id)`).
- Search intent claim creation (`status = 'selected'`) and automated abandoned claim recovery (reclaiming both stale `selected` and stale `started` executions exceeding the 15-minute lease boundary to `failed`).

### 2. Provider Routing & Quota Accounting
Before any external API call is initiated, the provider router ([app/services/provider_router.py](file:///home/vittal/Projects/jobpilot/Jobpilot/backend/app/services/provider_router.py)) selects an upstream provider:
- **Routing Order & Precedence**: Evaluates configured and enabled providers in priority order (`adzuna`, then `jooble`). A provider is selected only if its flag is enabled (`ADZUNA_ENABLED` / `JOOBLE_ENABLED`), its credentials are configured, and it possesses remaining quota capacity across minute, daily, weekly, monthly, and lifetime dimensions. If all enabled providers are exhausted, the cycle yields `daily_provider_budget_exhausted`.
- **Atomic Pre-emption**: Quota slots are committed to PostgreSQL *before* issuing any network request. Multi-day windows (`weekly`, `monthly`) utilize PostgreSQL advisory transaction locks (`pg_advisory_xact_lock`) to serialize concurrent checks per provider.
- **Fail-Safe Policy**: If the external provider returns an error, the quota remains consumed to prevent unbounded retry loops from draining upstream credit pools during provider outages.

### 3. Normalization & Ingestion
External provider listings are parsed into a canonical Pydantic model (`app.models.job.Job`):
- **Adzuna**: Ingested via REST API; maps salary ranges, publication timestamps, and extracts employment type (`full_time` $\to$ `Full-time`, `contract` $\to$ `Contract`, etc.) through [_parse_employment_type](file:///home/vittal/Projects/jobpilot/Jobpilot/backend/app/providers/adzuna.py).
- **Jooble**: Ingested via Indian region API (`in.jooble.org/api/{key}`); extracts job snippets and metadata; scrubs API credentials from all request logs.

### 4. Two-Tier Deduplication
Persistence in [app/db/repository/job_repository.py](file:///home/vittal/Projects/jobpilot/Jobpilot/backend/app/db/repository/job_repository.py) enforces two layers of deduplication:
1. **Source-Level Idempotency**: Strict unique constraint on `(source, source_job_id)` in the `job_sources` table.
2. **Deterministic Cross-Provider Canonical Deduplication**: A deterministic SHA-256 hash (`canonical_hash`) is generated from:
   - Normalized URL: Strips tracking queries (`utm_*`, `gclid`, `fbclid`, etc.) and ignores aggregator root domains (`adzuna.com`, `jooble.org`).
   - Fallback ETL Text: Normalizes alphanumeric tokens across `ETL|{company}|{title}|{location}`.

When a cross-provider duplicate is detected, JobPilot registers the secondary source in `job_sources` and skips duplicate row creation in `jobs`.

### 5. Fresher Eligibility Gate
Jobs processed for recommendation pass through the deterministic eligibility gate in [app/services/eligibility.py](file:///home/vittal/Projects/jobpilot/Jobpilot/backend/app/services/eligibility.py) (evaluated during enrichment and recommendation processing):
- **Seniority Rejection**: Rejects explicit seniority terms in the role title (`senior`, `sr`, `principal`, `staff`, `lead`, `manager`, `architect`, `head`, `director`, `vp`).
- **Numeric Experience Bounds**: Authoritatively rejects postings specifying $>1$ year of required experience; permits explicit $0\text{--}1$ year requirements.
- **Role Signals**: Recognizes fresher, entry-level, graduate, junior, and internship positions.
- **Fail-Closed Snippet Provenance**: When evaluating truncated snippets (`description_is_snippet = True`), description-derived positive signals are ignored unless confirmed in the authoritative title.
- **Company & History Disambiguation**: Distinguishes employer background, company founding years, collective team experience, and client track records from candidate requirements, preventing false rejections on business longevity unless applicant-governing signals apply.
- **Tightened Graduate & Trainee Scope**: Rejects administrative, operational, or recruiting positions merely targeting graduate/trainee audiences (e.g. `graduate recruiter`, `trainee program coordinator`), focusing strictly on legitimate entry-level vacancies.

### 6. Asynchronous Enrichment & SSRF Defense
Listings that enter the database with truncated snippet descriptions enqueue a row in `job_enrichments`. The standalone worker ([app/services/enrichment_worker.py](file:///home/vittal/Projects/jobpilot/Jobpilot/backend/app/services/enrichment_worker.py)) processes them:
- **Concurrency & Leases**: Claims pending tasks in batches using `FOR UPDATE SKIP LOCKED` with a lease expiration token.
- **SSRF Defense**: The [SSRFClient](file:///home/vittal/Projects/jobpilot/Jobpilot/backend/app/services/scraper/ssrf.py) blocks access to private subnets (RFC 1918), loopback addresses, link-local IPs, and cloud metadata endpoints (`169.254.169.254`).
- **Firecrawl Scraper Fallback**: When native fetching encounters non-terminal failures (HTTP 403, 401, 429) or extraction errors, the worker falls back to Firecrawl scraping (`FirecrawlClient`). Firecrawl requests atomically reserve credits against PostgreSQL `provider_usage` under `firecrawl_monthly` bounded by `FIRECRAWL_MONTHLY_BUDGET`.
- **Anti-Bot & Redirect Interstitial Rejection**: Scraped markdown is checked for anti-bot barriers ("checking your browser", "enable cookies"), redirect/interstitial wrappers ("you are now being redirected", "view ad here"), and length thresholds (<200 chars). Detections fail closed to prevent corrupted descriptions.
- **Graceful Snippet Fallback**: If scraping encounters an SSRF block, 404/410 errors, budget exhaustion, or content validation rejections, the worker calls `complete_with_snippet` (status `unsupported`) to evaluate the job using its snippet rather than abandoning score evaluation.

### 7. Deterministic Match Scoring
Scoring in [app/services/matching_service.py](file:///home/vittal/Projects/jobpilot/Jobpilot/backend/app/services/matching_service.py) evaluates a candidate job against configured operator preferences across up to five independent dimensions:
- **Title / Role Match** (up to 30 points): Regex word-boundary token matching against preferred roles.
- **Skills Match** (up to 30 points): Proportional matching of preferred technical skill tokens found in the job title and description.
- **Location Match** (up to 20 points): Substring matching against preferred locations.
- **Remote Policy** (up to 20 points): Alignment between operator preference (`remote` or `onsite`) and `job.remote` boolean (unspecified remote awards 10 points).
- **Experience Heuristic** (up to 20 points): Deterministic title keyword heuristic evaluating seniority markers against operator experience level.

The scoring engine dynamically accumulates the active maximum points (`max_score`) based on which preference dimensions the operator has populated, then normalizes the result to a $0\text{--}100$ scale:
$$\text{Final Score} = \left\lfloor \frac{\text{score}}{\text{max\_score}} \times 100 \right\rfloor$$
*(Note: Individual factor maxima sum to 120 points if all dimensions are active; they are not fixed weights summing to 100).* Qualified matches ($\text{Final Score} \ge 50$) are persisted in `recommendation_history` along with structured JSON `reasons` (e.g., `ROLE_MATCH`, `SKILLS_MATCH`, `LOCATION_MATCH`).

### 8. Notification Orchestration
JobPilot includes workflow definitions for n8n in `backend/`:
- **Search Cycle Workflow** (`backend/JP___Search_Cycle.json`): Iterates through `POST /ingestion/internal/select-next` and `POST /ingestion/internal/search`.
- **Notification Delivery Workflow** (`backend/JP___Notifications.json`): Designed to run on a 4-hour schedule; calls `POST /ingestion/internal/notifications/claim` to lock unsent recommendations, posts them to Telegram via bot credentials, and acknowledges delivery with at-most-once semantics (the Telegram send node does not configure automatic retries, and delivery state is tracked server-side to prevent duplicate re-delivery).

> [!NOTE]
> The workflow JSON files in `backend/` ship in an inactive state (`"active": false`). They define the automation logic and contracts, but are not actively executing out-of-the-box. They must be imported, configured with Telegram credentials, and activated within the n8n runtime environment.

---

## Providers & Quota Policies

JobPilot governs upstream provider quotas via a 3-tier policy model ([app/services/quota_policy.py](file:///home/vittal/Projects/jobpilot/Jobpilot/backend/app/services/quota_policy.py)):

$$\text{Effective Limit} = \min(\text{Provider Ceiling}, \text{Account Ceiling}, \text{Safety Budget})$$

```
┌────────────────────────────────────────────────────────┐
│                   PROVIDER CEILINGS                    │
│ Adzuna: 25/min, 250/day, 1000/week, 2500/month         │
│ Jooble: 500 lifetime                                   │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│                    ACCOUNT CEILINGS                    │
│ Optional operator-configured subscription boundaries   │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│                  SAFETY BUDGETS (App)                  │
│ Adzuna: 25 requests / day (default)                    │
│ Jooble: 2 requests / day, 500 lifetime (default)       │
└────────────────────────────────────────────────────────┘
```

---

## Deployment & Safety Engine

Production deployments are automated via [scripts/deploy.py](file:///home/vittal/Projects/jobpilot/Jobpilot/scripts/deploy.py) targeting an ARM64 host:

1. **Exact-SHA Release Identity**: Requires an exact 40-character Git commit SHA. Mutable tags such as `latest` are rejected.
2. **Architecture Validation**: Verifies that images target `linux/arm64`.
3. **Pre-flight Checks**: Acquires an advisory kernel lock (`fcntl.flock` on `.deploy.lock`) to prevent concurrent deployments and verifies $\ge 2\text{ GB}$ of free disk space.
4. **Pre-Migration Backup**: Executes `pg_dump -Fc` to create a binary PostgreSQL custom archive with SHA-256 checksum generation and `pg_restore -l` structural validation.
5. **Isolated Restore Verification**: Streams the generated backup into an ephemeral throwaway container (`jobpilot_verify_db_*`) executing `pg_restore --no-owner --no-privileges` to prove restore viability *before* production tables are touched.
6. **Database Migration**: Executes `alembic upgrade head` via the dedicated `migration` compose profile.
7. **Service Rollout & Health Polling**: Executes in-place Docker Compose service updates (`docker compose up -d --no-build`) and polls readiness healthchecks on FastAPI and Frontend.

> [!WARNING]
> Automated database rollbacks are intentionally not implemented. If a migration or container healthcheck fails, the deployment transitions to the `failed` state, halts rollout, and preserves the pre-migration backup for supervised manual recovery.

---

## CI/CD Supply Chain

The repository enforces automated validation across two GitHub Actions workflows:

```
[ Push to master ]
       │
       ▼
┌───────────────────────────────────────────────────────────┐
│                       CI Pipeline                         │
│  - Spin up PostgreSQL 16 Alpine service container         │
│  - Install dependencies from backend/requirements-dev.lock│
│  - Apply Alembic migrations (alembic upgrade head)        │
│  - Run 503 unit & integration tests with Pytest           │
│  - Validate Frontend (npm ci, oxlint, tsc -b, vite build) │
└─────────────────────────────┬─────────────────────────────┘
                              │ Passed
                              ▼
┌───────────────────────────────────────────────────────────┐
│                       CD Pipeline                         │
│  - Build ARM64 images on ubuntu-24.04-arm                 │
│  - Push images to GitHub Container Registry (GHCR)        │
│  - Verify published-vs-pulled image digest (FastAPI)      │
│  - Verify ARM64 architecture & commit SHA (All images)    │
│  - Package release tarball (compose, deploy.py, Caddyfile)│
│  - Stream release tarball over SSH to production host     │
└───────────────────────────────────────────────────────────┘
```

---

## Security Architecture

- **Network Containment**: Only Caddy binds to external host ports (`80`, `443`). PostgreSQL, FastAPI, n8n, and workers operate on internal non-exposed Docker bridge networks.
- **SSRF Hardening**: External URL scraping resolves DNS and validates IP ranges against loopback, RFC 1918 subnets, and AWS/GCP metadata endpoints (`169.254.169.254`).
- **Cryptographic Session Separation**:
  - **Main Application Session**: Uses a cryptographically secure random session token (`secrets.token_urlsafe(32)`), stored server-side in PostgreSQL as a one-way SHA-256 hash (`user_sessions` table), and delivered to the browser in a `Secure`, `HttpOnly`, `SameSite=Lax` cookie (`session_token`).
  - **Auth Gateway n8n Session**: Uses a separate, stateless HMAC-SHA256 signed session token (`jobpilot_admin_session`) generated with `AUTH_SECRET_KEY` and verified at the Caddy edge via `forward_auth`, delivered with `Secure`, `HttpOnly`, and `SameSite=Lax` flags.
- **Credential Hygiene**: Provider API keys are omitted or stripped from request URLs and error logging (e.g., in Jooble error handling), and endpoint exceptions fail closed with generic HTTP 500 error boundaries (`detail="Internal server error"`) to prevent internal traceback and secret leakage.
- **Constant-Time Verification**: Orchestration API keys and authentication tokens are evaluated using `secrets.compare_digest` to prevent timing attacks.

---

## Local Development

### Prerequisites
- Docker and Docker Compose
- Python 3.12+
- Node.js 20+

### Setup Instructions

1. **Clone the repository and configure environment variables**:
   ```bash
   cp .env.example .env
   # Edit .env with your local credentials and development keys
   ```

2. **Start the database and provisioning services**:
   ```bash
   docker compose up -d db db-setup
   ```

3. **Run database migrations**:
   ```bash
   PYTHONPATH=backend alembic -c backend/alembic.ini upgrade head
   ```

4. **Bootstrap the canonical Operator account (User 1)**:
   ```bash
   BOOTSTRAP_PASSWORD="your_dev_password" python scripts/bootstrap.py --email admin@jobpilot.local
   ```

5. **Start backend services**:
   ```bash
   docker compose up -d auth-gateway fastapi enrichment_worker
   ```
   *Alternatively, run FastAPI directly for development*:
   ```bash
   source venv/bin/activate
   pip install -r backend/requirements.txt
   PYTHONPATH=backend uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
   ```

6. **Start the Frontend development server**:
   ```bash
   cd frontend
   npm install
   npm run dev
   ```
   *The Vite dev server proxies `/api` requests to `127.0.0.1:8000` and `/login` requests to `127.0.0.1:8080`.*

---

## Testing & Quality Gates

The test suite covers provider parsing, quota concurrency, SSRF mitigation, candidate selection, eligibility logic, and API endpoints.

### Safety Invariant
The test runner enforces strict isolation in [conftest.py](file:///home/vittal/Projects/jobpilot/Jobpilot/conftest.py):
- The `TEST_DATABASE_URL` environment variable is mandatory.
- The target database name **must** contain `_test`.
- The suite aborts immediately if `TEST_DATABASE_URL` matches the application `DATABASE_URL` or default database names (`postgres`, `production`, `jobpilot`).

### Executing Backend Tests
```bash
export ENVIRONMENT="test"
export AUTH_SECRET_KEY="test"
TEST_DATABASE_URL="postgresql+psycopg://<user>:<password>@127.0.0.1:5432/<test_db_containing__test>" \
API_SECRET_KEY="<key_min_16_chars>" \
PYTHONPATH=backend:auth-gateway python -m pytest tests/ backend/tests/ -q
cd auth-gateway && PYTHONPATH=. python -m pytest tests/ -q
```
*(Release certification result: 503 passed in the backend/integration test suite, 20 passed in auth-gateway tests; 523 passed total.*

### Executing Frontend Validation
```bash
cd frontend
npm run lint      # oxlint
npm run build     # tsc -b && vite build
```

---

## Configuration Reference

Complete inventory of environment variables declared in [.env.example](file:///home/vittal/Projects/jobpilot/Jobpilot/.env.example):

| Variable | Scope | Purpose | Requirement / Format |
| :--- | :--- | :--- | :--- |
| `ENVIRONMENT` | Core | Runtime environment identifier | `production` / `development` |
| `DOMAIN` | Edge | Fully-qualified domain for Caddy TLS | Required for ACME |
| `ACME_EMAIL` | Edge | Contact email for Let's Encrypt / ZeroSSL | Valid email format |
| `CADDY_ADMIN_USER` | Gateway | Operator username for n8n edge forward auth | Required |
| `CADDY_ADMIN_HASH` | Gateway / Deploy | Bcrypt hash of operator password (retained for production configuration contract and deployment pre-flight validation) | Required |
| `CADDY_ADMIN_HASH_B64` | Gateway | Base64-encoded bcrypt hash of operator password for Auth Gateway | Required |
| `POSTGRES_DB` | Database | Primary application database name | `jobpilot` |
| `POSTGRES_USER` | Database | Primary application database user | `jobpilot` |
| `POSTGRES_PASSWORD` | Database | PostgreSQL database password | High-entropy secret |
| `N8N_DB_NAME` | Database | Isolated database for n8n workflows | `n8n_data` |
| `N8N_DB_USER` | Database | Database user dedicated to n8n | `n8n_user` |
| `N8N_DB_PASSWORD` | Database | Database password for n8n user | High-entropy secret |
| `N8N_ENCRYPTION_KEY` | n8n | Key for encrypting n8n credentials at rest | High-entropy string |
| `AUTH_SECRET_KEY` | Gateway | Secret key for HMAC signing of Auth Gateway cookies | High-entropy secret |
| `API_SECRET_KEY` | Core / n8n | Internal orchestration API key (`X-Api-Key`) | Min 16 chars in production |
| `ADZUNA_APP_ID` | Providers | Adzuna API Application ID | Optional if disabled |
| `ADZUNA_APP_KEY` | Providers | Adzuna API Secret Key | Optional if disabled |
| `ADZUNA_ENABLED` | Providers | Toggle Adzuna ingestion | `true` / `false` |
| `JOOBLE_IN_API_KEY` | Providers | Jooble India API key (from `in.jooble.org`) | Optional if disabled |
| `JOOBLE_ENABLED` | Providers | Toggle Jooble ingestion | `true` / `false` |
| `APP_COMMIT_SHA` | Build | Exact 40-character Git commit SHA | Set by CI/CD build |
| `BACKUP_ENCRYPTION_KEY` | Backups | Symmetric key for database backup encryption | Optional |
| `BACKUP_S3_ACCESS_KEY` | Backups | Object storage access key for offsite backups | Optional |
| `BACKUP_S3_SECRET_KEY` | Backups | Object storage secret key for offsite backups | Optional |
| `TELEGRAM_CHAT_ID` | Telegram | Target chat/channel ID for n8n notifications | Optional |
| `FIRECRAWL_API_KEY` | Scraper | Firecrawl API key (`https://api.firecrawl.dev`) for scraping fallback | Optional if disabled |
| `FIRECRAWL_MONTHLY_BUDGET` | Scraper | Monthly credit budget for Firecrawl scraping fallback | Optional (Default: `1000`) |

---

## Operational Status & Limitations

- **Single-Operator Architecture**: The database and ingestion flows assume a single active operator (User 1). Multi-tenant isolation is not implemented.
- **Workflow Activation**: The n8n workflow definitions in `backend/` ship in an inactive state (`"active": false`). To initiate automated search loops and notifications, credentials (including Telegram bot tokens) must be configured in n8n and workflows manually activated.
- **Rollout Mechanics**: Deployments use in-place Docker Compose service recreations rather than blue/green deployments. Service updates require brief container recreation, monitored by healthchecks.
- **Provider Geographic Constraints**: Jooble API keys are region-locked to `in.jooble.org`. Adzuna ingestion is bound by safety budgets to prevent unexpected provider billing.
