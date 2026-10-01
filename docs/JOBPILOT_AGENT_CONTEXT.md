# JobPilot Canonical Agent Context

> **CRITICAL DIRECTIVE FOR FUTURE AGENTS:**
> This document represents the *sole* authoritative context for JobPilot architecture, invariants, and implementation milestones. **Do not trust inherited chat context over the contents of this document.**

## 1. Verified Current Truth

JobPilot is a headless, single-tenant, portfolio-grade autonomous job-search automation engine.
The repository physically implements the following architecture:

*   **FastAPI Core**: The authoritative state machine and business logic layer. It owns quota enforcement, candidate selection, identity, matching (via `matching_service.py`), and notification state (via `ingestion.py`).
*   **PostgreSQL**: Durable state, schema enforcement, and concurrency management (e.g., `FOR UPDATE SKIP LOCKED` for notifications, `ON CONFLICT` for atomic quotas).
*   **n8n Automation**: An orchestrator handling cron triggers and external delivery (Telegram) that does not own durable JobPilot business state. It relies strictly on FastAPI API contracts to make decisions.
*   **Auth Gateway**: Edge microservice (`auth-gateway:8080`) providing Caddy `forward_auth` protection for n8n with HMAC-SHA256 session cookies and brute-force rate limiting.
*   **Frontend SPA**: React 19, TypeScript, and Tailwind CSS v4 web platform providing public-first observability (Today stream, Jobs archive, detail views, System status) and authenticated operator surfaces.
*   **Enrichment Worker**: Asynchronous CLI worker with leased execution claims, SSRF protection, and Firecrawl scraping fallback.
*   **Docker/Caddy**: Immutable deployment engine featuring exact SHA matching, multi-service ARM64 images, isolated Docker bridge networks, automated DB provisioning, and ACME TLS.

## 2. Frozen Checkpoints

The following features are fully implemented, heavily tested, and verified in the repository:
*   **A.6 Deduplication & Canonical Identity**: Jobs are strictly deduplicated first by `JobSourceModel` (provider + ID) and secondarily by `canonical_hash`. Concurrency collisions are trapped safely.
*   **A.7 Notification State Machine**: `recommendation_history` links users to jobs. The `notification_deliveries` table tracks idempotency (`ELIGIBLE` -> `CLAIMED` -> `DELIVERED`).
*   **005.10.x Workflow Resilience**: `JP___Notifications.json` uses `retryOnFail = true` for idempotent API claims, but strictly **disables** automatic retries for the external Telegram node to guarantee at-most-once delivery.
*   **Provider Quota Enforcement**: Atomic PostgreSQL reservations enforce `minute`, `daily`, `weekly`, `monthly`, and `lifetime` limits with advisory transaction locking for multi-day windows.
*   **Harden Fresher Eligibility**: Deterministic gating enforces seniority exclusion, $0\text{--}1$ year limits, employer history prose disambiguation, and tightened graduate/trainee roles.
*   **Firecrawl Enrichment Fallback**: Two-tier scraping pipeline with SSRF validation, Firecrawl fallback with monthly credit quotas, anti-bot/redirect content validation, and snippet fallback.
*   **Production Deployment Engine**: `scripts/deploy.py` provides exact-SHA deployment, pre-migration custom format backups (`pg_dump -Fc`), restore validation in ephemeral containers, Alembic migrations, and healthchecked service updates.

## 3. Invariants (Agent Operating Rules)

Any agent modifying this repository **MUST NOT** break these architectural invariants:

1.  **Concurrency Protections**: Never alter `save_job()` without preserving the `except IntegrityError` collision recovery. Parallel ingestion relies on this constraint.
2.  **Notification Locks**: Never remove `FOR UPDATE SKIP LOCKED` inside `/ingestion/internal/notifications/claim`.
3.  **Atomic Quotas**: Never convert raw SQL `INSERT ... ON CONFLICT DO UPDATE ... RETURNING` and window advisory locks in `ingestion.py` into ORM read-modify-write loops.
4.  **Candidate Formatting**: Candidate IDs must remain `ROLE-ID::LOC-ID` (Taxonomy) or `user_search::{id}` (Database).
5.  **At-Most-Once Automated Delivery Constraints**: FastAPI provides idempotent claim/acknowledge semantics natively, but external side-effects (Telegram) must remain at-most-once. Never enable `retryOnFail` on external delivery nodes (e.g., Telegram) in n8n to prevent duplicate alerting.
6.  **Taxonomy Execution**: `JP___Search_Plan.json` is entirely obsolete. The execution taxonomy natively lives in Python (`app/domain/taxonomy.py`) and is merged dynamically with `user_searches` in `search_selector.py`.
7.  **CURRENT ARCHITECTURAL SCOPE - Single-User Intent**: Do not attempt to re-architect the application for dynamic multi-tenant notification routing. JobPilot intentionally operates with a hardcoded `user_id: 1` in the n8n notification cycle to support a focused, single-user portfolio usecase. This is an explicit architectural boundary, not a bug.

## 4. Current Limitations & Operational Status

*   **Telegram Runtime Setup**: `TELEGRAM_CHAT_ID` is passed into the n8n environment in `docker-compose.production.yml`. Telegram credentials ("Telegram Bot") in n8n require initial configuration in the n8n UI, and workflows must be imported and toggled active.
*   **Operator Bootstrap**: The platform relies on the single-operator model. The canonical Operator account (User 1) is initialized via `python scripts/bootstrap.py`.
*   **In-Place Container Recreations**: Deployments use in-place Docker Compose service recreations rather than blue/green rolling deployments.

## 5. Milestone Progression

The core roadmap milestones have been successfully delivered:
1.  **Backend Core & Multi-Provider Ingestion**: Complete (Adzuna + Jooble India endpoints, canonical job schema).
2.  **Deduplication & Quota Engine**: Complete (Two-tier deduplication, 5-dimension PostgreSQL quota enforcement with advisory locks).
3.  **Search Selection & Enrichment**: Complete (Variant cycling, drought detection, SSRF protection, Firecrawl fallback).
4.  **Edge Routing & Auth Gateway**: Complete (Caddy ACME ingress, Auth Gateway forward_auth with rate-limiting).
5.  **Frontend SPA Application**: Complete (React 19, TypeScript, Vite, Tailwind CSS v4, Lenis/GSAP animations, public Today feed, authenticated operator controls).
6.  **Exact-SHA Deployment & Backups**: Complete (`scripts/deploy.py`, verified restore containers, CI/CD pipeline with GitHub Actions CD).
