# JobPilot Canonical Agent Context

> **CRITICAL DIRECTIVE FOR FUTURE AGENTS:**
> This document represents the *sole* authoritative context for JobPilot architecture, invariants, and implementation milestones. **Do not trust inherited chat context over the contents of this document.**

## 1. Verified Current Truth

JobPilot is a headless, single-tenant, portfolio-grade autonomous job-search automation engine.
The repository physically implements the following architecture:

*   **FastAPI Core**: The authoritative state machine and business logic layer. It owns quota enforcement, candidate selection, identity, matching (via `matching_service.py`), and notification state (via `ingestion.py`).
*   **PostgreSQL**: Durable state, schema enforcement, and concurrency management (e.g., `FOR UPDATE SKIP LOCKED` for notifications, `ON CONFLICT` for atomic quotas).
*   **n8n Automation**: An orchestrator handling cron triggers and external delivery (Telegram) that does not own durable JobPilot business state. It relies strictly on FastAPI API contracts to make decisions.
*   **Docker/Caddy**: Immutable deployment engine featuring exact SHA matching, isolated backend networks, and automated DB provisioning.

## 2. Frozen Checkpoints

The following features are fully implemented, heavily tested, and verified in the repository:
*   **A.6 Deduplication & Canonical Identity**: Jobs are strictly deduplicated first by `JobSourceModel` (provider + ID) and secondarily by `canonical_hash`. Concurrency collisions are trapped safely.
*   **A.7 Notification State Machine**: `recommendation_history` links users to jobs. The `notification_deliveries` table tracks idempotency (`ELIGIBLE` -> `CLAIMED` -> `DELIVERED`).
*   **005.10.x Workflow Resilience**: `JP___Notifications.json` uses `retryOnFail = true` for idempotent API claims, but strictly **disables** automatic retries for the external Telegram node to guarantee at-most-once delivery.

## 3. Invariants (Agent Operating Rules)

Any agent modifying this repository **MUST NOT** break these architectural invariants:

1.  **Concurrency Protections**: Never alter `save_job()` without preserving the `except IntegrityError` collision recovery. Parallel ingestion relies on this constraint.
2.  **Notification Locks**: Never remove `FOR UPDATE SKIP LOCKED` inside `/internal/notifications/claim`.
3.  **Atomic Quotas**: Never convert raw SQL `INSERT ... ON CONFLICT DO UPDATE ... RETURNING` in `quota_policy.py` into ORM read-modify-write loops.
4.  **Candidate Formatting**: Candidate IDs must remain `ROLE-ID::LOC-ID` (Taxonomy) or `user_search::{id}` (Database).
5.  **At-Most-Once Automated Delivery Constraints**: FastAPI provides idempotent claim/acknowledge semantics natively, but external side-effects (Telegram) must remain at-most-once. Never enable `retryOnFail` on external delivery nodes (e.g., Telegram) in n8n to prevent duplicate alerting.
6.  **Taxonomy Execution**: `JP___Search_Plan.json` is entirely obsolete. The execution taxonomy natively lives in Python (`app/domain/taxonomy.py`) and is merged dynamically with `user_searches` in `search_selector.py`.
7.  **CURRENT ARCHITECTURAL SCOPE - Single-User Intent**: Do not attempt to re-architect the application for dynamic multi-tenant notification routing. JobPilot intentionally operates with a hardcoded `user_id: 1` in the n8n notification cycle to support a focused, single-user portfolio usecase. This is an explicit architectural boundary, not a bug.

## 4. Current Limitations

*   **Unresolved Telegram Integration Design**: The Telegram node in `JP___Notifications.json` uses `={{ $env.TELEGRAM_CHAT_ID }}` and a `telegramApi` credential ("Telegram Bot"). However, `TELEGRAM_CHAT_ID` is not currently passed to n8n at runtime, and the credential requires manual UI setup. An implementation decision is needed on how to securely provide and inject these values (e.g., programmatic bootstrapping vs. documented manual setup) to achieve full autonomy.
*   **Bootstrapping Gap**: The internal automation logic is thoroughly tested, but without an initial `User 1` and corresponding `UserSearch` records seeded in the database, the ingestion engine has no personalized search parameters to execute against. Furthermore, real-world end-to-end Telegram behavior remains unvalidated in production.

## 5. Roadmap & Exact Next Milestones

Before any frontend is built, the immediate roadmap for genuine autonomy is:

1.  **005.10.3 Real Telegram Integration/Configuration**: Resolve the configuration, environment passthrough (`TELEGRAM_CHAT_ID`), and credential management gap. Create the initial seeder for `user_id: 1` to close the bootstrapping gap.
2.  **Real End-To-End Telegram Validation**: Validate the entire notification lifecycle end-to-end against live Telegram APIs.
3.  **005.11 VPS Deployment**: Deploy the headless engine to the target VPS environment.
4.  **005.12 Production Security Audit**: Perform a rigorous audit of the deployed production state.
5.  **Frontend Application (Later)**: Once the backend loop is entirely self-sufficient in production, build the web frontend client.
