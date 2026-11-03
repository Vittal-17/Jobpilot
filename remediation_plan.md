# Remediation Implementation Plan: JobPilot P0/Firecrawl

> Prepared based on `.gsd/ARCHITECTURE.md`, `.gsd/STACK.md`, and repository review.

---

## Phase 1: H18 Linear-Time Content Validation
- **Objective:** Eliminate catastrophic or superlinear regular expression backtracking risk in job posting content validation and title scanning.
- **Invariants:** Validation scanner must be provably linear $O(n)$ with respect to payload input length. No regex pattern with nested quantifiers or overlapping alternation allowed on unbounded inputs.
- **Current Implementation to Reuse:** Non-regex parsing routines in [`backend/app/services/content_scanner.py`](backend/app/services/content_scanner.py:1) (`has_content_aggregate_count()`, `title_aggregate_reason()`, `_space_end()`, `_literal_end()`).
- **Exact File/Function Scope:**
  - [`backend/app/services/content_scanner.py`](backend/app/services/content_scanner.py:1) (`has_content_aggregate_count`, `title_aggregate_reason`, `validate_posting_content`)
  - [`backend/app/services/enrichment_worker.py:validate_posting_content()`](backend/app/services/enrichment_worker.py:65)
- **Database / Migration Impact:** None.
- **Transaction Boundaries:** Pure in-memory validation routines; no DB transactions or locks.
- **Focused Tests:** [`backend/tests/services/test_enrichment_content_validation.py`](backend/tests/services/test_enrichment_content_validation.py:1)
- **Adversarial Tests & Acceptance Probes:** Exact 1 KiB, 2 KiB, 4 KiB, 8 KiB, and 16 KiB adversarial regression probes with nested/repeated string patterns (e.g., `("jobs in " * N)`). Verify execution time grows linearly with input size without exponential backtracking.
- **Failure Behavior:** Fail closed; invalid or malicious content returns `(False, reason_code)`.
- **Dependencies:** None (Phase 1).
- **Explicit Completion Criteria:** 1/2/4/8/16 KiB adversarial regression probe suite passes with linear execution profile; zero unhandled regex timeouts.
- **Explicit Non-Goals:** Modifying high-level content validation business rules or decision contracts for valid postings.

---

## Phase 2: H19 Bounded Firecrawl Transport & Decompression
- **Objective:** Protect system resources against HTTP response payload bombs, zip bombs, and non-finite JSON injection during Firecrawl API calls.
- **Invariants:** Streaming-before-materialization is the primary invariant:
  1. Declared Content-Length rejection if header exceeds max allowed wire bytes.
  2. Wire-byte streaming limit enforcement during body download.
  3. Bounded decompression ratio and streaming decompression chunk limits.
  4. Hard cap on total uncompressed decoded bytes.
  5. Bounded buffer before JSON parsing.
  6. Rejection of non-finite JSON constants (`NaN`, `Infinity`, `-Infinity`).
- **Current Implementation to Reuse:** [`backend/app/services/scraper/firecrawl.py:FirecrawlClient._read_streamed_body()`](backend/app/services/scraper/firecrawl.py:114), `_decode_json()`, `_decoder()`.
- **Exact File/Function Scope:**
  - [`backend/app/services/scraper/firecrawl.py:FirecrawlClient`](backend/app/services/scraper/firecrawl.py:48) (`_read_streamed_body`, `_decode_json`, `_decoder`, `_post_json`)
- **Database / Migration Impact:** None.
- **Transaction Boundaries:** Network transport and response parsing level; no database transactions.
- **Focused Tests:** [`backend/tests/p0/test_h18_h19_payload_bounds.py`](backend/tests/p0/test_h18_h19_payload_bounds.py:1), [`backend/tests/services/test_firecrawl_client.py`](backend/tests/services/test_firecrawl_client.py:1)
- **Adversarial Tests & Acceptance Probes:**
  - Stream a 100MB compressed gzip stream yielding high compression ratio; verify streaming halts when decoded byte limit or ratio threshold is hit.
  - Inject raw JSON containing `NaN` / `Infinity`; verify custom object decoder rejects with `ValueError` / `ProviderPayloadError`.
- **Failure Behavior:** Raise `ProviderPayloadError` on any bound violation, aborting connection immediately without allocating full memory.
- **Dependencies:** True prerequisite: none; preferred order: after Phase 1.
- **Explicit Completion Criteria:** Payload bounding probes halt streamed responses at exact byte thresholds; non-finite JSON values rejected.
- **Explicit Non-Goals:** Changing external Firecrawl request payload construction or return schemas.

---

## Phase 3: H20 Enrichment Ownership & Terminal Fencing
- **Objective:** Ensure single-worker execution ownership across the entire enrichment lifecycle, preventing stale workers from making paid provider calls or committing updates after lease expiration or state transition.
- **Invariants:**
  1. Live ownership required at every boundary: `status == "in_progress"` AND matching `generation`/`lease_token` AND unexpired lease duration.
  2. Paid API provider reservations must atomically revalidate live ownership immediately prior to invoking external network calls.
  3. Terminal transitions must be fenced exactly once, clearing lease fields atomically, and suppressing all stale side effects or late commits.
- **Current Implementation to Reuse:** [`backend/app/services/enrichment_worker.py:EnrichmentWorker.is_lease_live()`](backend/app/services/enrichment_worker.py:255), `fence_terminal_transition()`.
- **Exact File/Function Scope:**
  - [`backend/app/services/enrichment_worker.py:EnrichmentWorker`](backend/app/services/enrichment_worker.py:139) (`process_job`, `_reserve_firecrawl_receipt`, `fence_terminal_transition`, `complete_success`, `complete_with_snippet`, `complete_failure`)
- **Database / Migration Impact:** None.
- **Transaction Boundaries:** Atomic SQL `UPDATE ... WHERE id = :job_id AND lease_token = :token AND status = 'in_progress'` queries per transition.
- **Focused Tests:** [`backend/tests/p0/test_h20_paid_call_fences.py`](backend/tests/p0/test_h20_paid_call_fences.py:1), [`backend/tests/services/test_enrichment_worker.py`](backend/tests/services/test_enrichment_worker.py:1)
- **Adversarial Tests & Acceptance Probes:**
  - Expired-lease probe: Simulate expired worker lease prior to provider reservation; verify worker exits with ZERO paid calls, zero API usage, and zero reservation receipts created.
  - Concurrency lease theft probe: Revoke worker A lease while in-flight; verify worker A's late completion attempt is fenced and returns 0 rows updated without modifying job state.
- **Failure Behavior:** Stale worker updates fail silently (0 rows updated), log warning, and exit without altering state or invoking external APIs.
- **Dependencies:** True prerequisite: Phase 2 (for bounded Firecrawl client calls during enrichment). Preferred order: Phase 3.
- **Explicit Completion Criteria:** Expired-lease acceptance probe passes with 0 paid provider calls; race condition fencing tests pass 100%.
- **Explicit Non-Goals:** Modifying `JobEnrichmentModel` table schema.

---

## Phase 4: SSRF Request-Local Connection Binding & Proxy Isolation
- **Objective:** Eliminate SSRF vulnerabilities by enforcing strict request-local IP resolution and socket binding, redirect revalidation, and peer verification.
- **Invariants:**
  1. Request-local DNS resolution and connect-time IP binding (no global socket/resolver monkey-patching).
  2. `trust_env=False` strictly enforced on underlying HTTP clients to prevent HTTP proxy hijacking via environment variables.
  3. Actual-peer verification after connection setup; fail closed if peer IP metadata is missing or invalid.
  4. Full IP validation re-executed on every HTTP redirect hop prior to opening subsequent connections.
- **Current Implementation to Reuse:** [`backend/app/services/scraper/ssrf.py:SSRFClient`](backend/app/services/scraper/ssrf.py:105), `SSRFTransport`, `_validate_ip()`, `_resolve_and_validate()`.
- **Exact File/Function Scope:**
  - [`backend/app/services/scraper/ssrf.py`](backend/app/services/scraper/ssrf.py:1) (`SSRFClient`, `SSRFTransport`, `_validate_url`, `_verify_peer`, `fetch`)
- **Database / Migration Impact:** None.
- **Transaction Boundaries:** HTTP transport layer; no database transactions.
- **Focused Tests:** [`backend/tests/p0/test_ssrf_and_notification_contracts.py`](backend/tests/p0/test_ssrf_and_notification_contracts.py:1), [`backend/tests/services/test_scraper_security_contract.py`](backend/tests/services/test_scraper_security_contract.py:1)
- **Adversarial Tests & Acceptance Probes:**
  - Attempt connecting to loopback (`127.0.0.1`, `::1`), private (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), and AWS IMDS (`169.254.169.254`).
  - DNS rebinding probe: Serve a domain with initial public IP that redirects to `127.0.0.1`; verify hop-by-hop revalidation blocks the redirect.
  - Concurrent request isolation test under high thread count.
- **Failure Behavior:** Raise `SSRFProtectionError` / `HTTPException(400)` fail-closed whenever a target IP resolves to restricted ranges or missing peer metadata.
- **Dependencies:** True prerequisite: none. Preferred order: Phase 4.
- **Explicit Completion Criteria:** SSRF test suite and DNS rebinding probes pass 100% with zero unhandled network leaks.
- **Explicit Non-Goals:** Adding external proxy authentication endpoints.

---

## Phase 5: H07 Provider Billing & Reservation Truth
- **Objective:** Enforce exact provider credit accounting, reservation receipts, lifecycle tracking, and crash-recovery settlement.
- **Invariants:**
  1. Authoritative Firecrawl billing-contract verification (`scrape_billing_contract_verified()`) is a prerequisite for paid operations; if unprovable, fail closed and deny paid work.
  2. Credit reservation receipts must track unique operation keys and transition through defined lifecycle states (`reserved` -> `in_flight` -> `settled` / `failed` / `unknown`).
  3. Crashed or ambiguous reservations must be settled as `unknown` without corrupting daily/monthly usage metrics.
  4. Quotas must be checked and updated using atomic SQL `INSERT ... ON CONFLICT DO UPDATE` or transaction advisory locks.
- **Current Implementation to Reuse:** [`backend/app/services/firecrawl_quota.py`](backend/app/services/firecrawl_quota.py:1) (`reserve_provider_request`, `settle_provider_reservation`, `mark_provider_request_in_flight`, `latch_firecrawl_payment_required`).
- **Exact File/Function Scope:**
  - [`backend/app/services/firecrawl_quota.py`](backend/app/services/firecrawl_quota.py:1)
  - [`backend/app/services/ingestion.py`](backend/app/services/ingestion.py:1) (`reserve_provider_request`, `acquire_provider_request_slot`)
  - [`backend/app/providers/firecrawl.py`](backend/app/providers/firecrawl.py:1)
- **Database / Migration Impact:** Verify schema support for `provider_reservation_receipts` or `firecrawl_operations` table tracking operation keys and status.
- **Transaction Boundaries:** Atomic SQL upserts on `provider_minute_usage` and `provider_usage` tables within explicit DB sessions.
- **Focused Tests:** [`backend/tests/services/test_firecrawl_quota_foundation.py`](backend/tests/services/test_firecrawl_quota_foundation.py:1), [`backend/tests/services/test_t2_paid_lifecycle.py`](backend/tests/services/test_t2_paid_lifecycle.py:1), [`backend/tests/services/test_ingestion_quota.py`](backend/tests/services/test_ingestion_quota.py:1)
- **Adversarial Tests & Acceptance Probes:**
  - Unverified billing contract probe: Set unverified contract flag; assert paid request is denied fail-closed with `ProviderBillingContractError`.
  - Worker crash simulation: Reserve credits, simulate unhandled exception before settlement; verify recovery routine marks reservation as `unknown` and updates usage telemetry accurately.
- **Failure Behavior:** Fail closed on unverified contracts or quota exhaustion with `PaymentRequired` or `RateLimitExceeded`.
- **Dependencies:** True prerequisite: Phase 2 (bounded transport). Preferred order: Phase 5.
- **Explicit Completion Criteria:** Reservation lifecycle, crash settlement, and unverified contract probes pass 100%.
- **Explicit Non-Goals:** Altering quota limits defined in `settings`.

---

## Phase 6: Firecrawl 402 Propagation & Durable Exhaustion
- **Objective:** Propagate HTTP 402 Payment Required status through all service layers, set a durable database payment latch, and block all subsequent paid provider attempts.
- **Invariants:**
  1. Receipt of HTTP 402 from Firecrawl must preserve 402 status code across all exception and response boundaries.
  2. Database payment latch (`payment_required = True` in `provider_state`) must be written durably.
  3. Latch persistence failure must fail closed (deny paid calls even if state save fails).
  4. Subsequent paid provider requests (including concurrent racing requests) must check the payment latch and fast-fail with HTTP 402 without making external network calls.
- **Current Implementation to Reuse:** [`backend/app/services/firecrawl_quota.py:latch_firecrawl_payment_required()`](backend/app/services/firecrawl_quota.py:197), [`get_firecrawl_budget_state()`](backend/app/services/firecrawl_quota.py:427).
- **Exact File/Function Scope:**
  - [`backend/app/services/firecrawl_quota.py`](backend/app/services/firecrawl_quota.py:1) (`latch_firecrawl_payment_required`, `ensure_firecrawl_budget`)
  - [`backend/app/providers/firecrawl.py`](backend/app/providers/firecrawl.py:1) (`_raise_http_error`, `search`)
  - [`backend/app/api/endpoints/ingestion.py`](backend/app/api/endpoints/ingestion.py:85) (`ingest_firecrawl`)
- **Database / Migration Impact:** None. Uses existing `provider_state` table.
- **Transaction Boundaries:** Immediate commit of payment latch to `provider_state` using an independent session/engine.
- **Focused Tests:** [`backend/tests/p0/test_provider_402_and_discovery_replay.py`](backend/tests/p0/test_provider_402_and_discovery_replay.py:1), [`backend/tests/services/test_firecrawl_provider.py`](backend/tests/services/test_firecrawl_provider.py:1)
- **Adversarial Tests & Acceptance Probes:**
  - Trigger mock 402 response from Firecrawl; verify `payment_required` flag is latched in `provider_state`.
  - Concurrent race probe: Execute 5 parallel requests after latch is active; verify all 5 fail fast with 402 without sending any HTTP requests to Firecrawl.
- **Failure Behavior:** Return HTTP 402 Payment Required immediately when payment latch is set.
- **Dependencies:** True prerequisite: Phase 5 (billing & quota foundation). Preferred order: Phase 6.
- **Explicit Completion Criteria:** Durable 402 payment latch and concurrent fast-fail probes pass 100%.
- **Explicit Non-Goals:** Modifying provider routing for non-Firecrawl providers.

---

## Phase 7: Discovery Execution Idempotency
- **Objective:** Ensure discovery execution runs are strictly idempotent using stable run IDs, plan payload hashing, atomic claims, query operation keys, and stored receipts.
- **Invariants:**
  1. Discovery runs must require a stable `run_id` and authoritative `plan_hash`.
  2. Claiming a discovery run (`_claim_discovery_run()`) must be atomic in PostgreSQL (`INSERT ... ON CONFLICT DO NOTHING`).
  3. Replay requests with identical `plan_hash` must return stored telemetry/receipts without re-executing paid queries.
  4. Concurrent claims on the same `run_id` must detect conflict, wait, or yield the cached result.
  5. Interrupted or crashed runs must record unknown status to prevent stuck states.
- **Current Implementation to Reuse:** [`backend/app/services/firecrawl_discovery.py`](backend/app/services/firecrawl_discovery.py:1) (`_plan_hash`, `_claim_discovery_run`, `_finish_discovery_run`, `execute_discovery_run`).
- **Exact File/Function Scope:**
  - [`backend/app/services/firecrawl_discovery.py`](backend/app/services/firecrawl_discovery.py:1)
  - [`backend/app/api/endpoints/ingestion.py`](backend/app/api/endpoints/ingestion.py:496) (`internal_plan_firecrawl_discovery`, `internal_run_firecrawl_discovery`)
- **Database / Migration Impact:** None. Uses `firecrawl_discovery_runs` table.
- **Transaction Boundaries:** Atomic insertion and updates on `firecrawl_discovery_runs` table per discovery phase.
- **Focused Tests:** [`backend/tests/services/test_firecrawl_discovery_idempotency.py`](backend/tests/services/test_firecrawl_discovery_idempotency.py:1), [`backend/tests/p0/test_provider_402_and_discovery_replay.py`](backend/tests/p0/test_provider_402_and_discovery_replay.py:1)
- **Adversarial Tests & Acceptance Probes:**
  - Sequential replay probe: Call `/internal/firecrawl-discovery/run` twice with identical plan; verify second call returns cached receipt with 0 additional credit consumption.
  - Concurrent claim race probe: Invoke 3 simultaneous runs for same `run_id`; verify exactly one executes and others return the stored outcome.
- **Failure Behavior:** Return existing run telemetry or raise HTTP 409/500 on unresolvable conflicts.
- **Dependencies:** True prerequisite: Phase 5 & 6. Preferred order: Phase 7.
- **Explicit Completion Criteria:** Discovery replay and concurrent claim idempotency tests pass 100%.
- **Explicit Non-Goals:** Changing discovery taxonomy generation logic.

---

## Phase 8: Notification Ownership & Delivery Truth
- **Objective:** Enforce explicit notification delivery state transitions (`claimed` -> `send_started` -> `acknowledged` / `definitive_failed` / `unknown`), destination ownership isolation, and semantic Telegram response verification.
- **Invariants:**
  1. Notification lifecycle must follow strict states: `claimed` -> `send_started` -> `acknowledged` / `definitive_failed` / `unknown`.
  2. Single-operator destination ownership (`user_id == owner_id`) enforced on all notification endpoints (`claim`, `send-start`, `acknowledge`, `fail`).
  3. Semantic Telegram response verification: Delivery acknowledgment requires HTTP 200 with JSON `ok: true` and valid integer `message_id`.
  4. Ambiguous or timed-out sends must be recorded as `unknown` and reconciled via stale claim recovery (`recover_stale_pre_send_claims()`).
- **Current Implementation to Reuse:** [`backend/app/api/endpoints/ingestion.py`](backend/app/api/endpoints/ingestion.py:799) (`_require_notification_owner`, `claim_notifications`, `start_notification_send`, `acknowledge_notifications`, `fail_notifications`, `recover_stale_pre_send_claims`).
- **Exact File/Function Scope:**
  - [`backend/app/api/endpoints/ingestion.py`](backend/app/api/endpoints/ingestion.py:799)
  - [`backend/app/db/models/notification_delivery.py`](backend/app/db/models/notification_delivery.py:1)
- **Database / Migration Impact:** Ensure `notification_deliveries` table has `send_started_at` and `is_durable_unknown` support.
- **Transaction Boundaries:** `FOR UPDATE SKIP LOCKED` inside `claim_notifications()` transaction; advisory xact locks on `hashtext('notifications')`.
- **Focused Tests:** [`backend/tests/p0/test_notification_owner_and_send_fence.py`](backend/tests/p0/test_notification_owner_and_send_fence.py:1), [`backend/tests/api/test_notification_owner_policy.py`](backend/tests/api/test_notification_owner_policy.py:1), [`tests/test_a7_notifications.py`](tests/test_a7_notifications.py:1)
- **Adversarial Tests & Acceptance Probes:**
  - Destination ownership violation probe: Attempt claiming/acknowledging notifications with unauthorized `user_id`; verify HTTP 403 Forbidden.
  - Telegram response validation probe: Provide mock Telegram response with HTTP 200 but `ok: false` or missing `message_id`; verify system transitions to `definitive_failed` / `unknown` rather than `acknowledged`.
  - Stale send recovery probe: Simulate worker crash during `send_started`; run recovery and verify delivery transitions to `unknown`.
- **Failure Behavior:** Reject unauthorized users with HTTP 403; handle delivery failure without duplicate notifications.
- **Dependencies:** True prerequisite: None. Preferred order: Phase 8 (final integration step).
- **Explicit Completion Criteria:** `./scripts/ralph-check.sh` passes 100% across all suites; notification delivery state machine and ownership probes pass.
- **Explicit Non-Goals:** Converting single-operator model (`user_id: 1`) to dynamic multi-tenant notification engine.
