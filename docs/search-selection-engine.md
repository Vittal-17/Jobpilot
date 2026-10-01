# Search Selection Engine (005.7 / 005.8)

## Overview

The Search Selection Engine governs exactly *what* JobPilot searches for, *who* performs the search, and *how many* searches occur per run.

The architecture is explicitly separated:
- **005.6:** Provider quota authority and execution.
- **005.7:** Candidate selection (deterministic).
- **005.7B:** Provider routing intelligence.
- **005.8:** Bounded cycle orchestration.

## 005.7 Candidate Selection & Variant Telemetry

Candidates are generated dynamically from the active domain taxonomy (`cid = {role_id}::{location_id}`).
They are scored and prioritized based on:
1. **Base Priority & Location Tier**: `score = (priority * 100) + (tier * 10)`. Lower scores evaluate first.
2. **Freshness & Exploration Boosts**: Candidates never selected receive a `-1000` boost; candidates never succeeded receive a `-500` boost.
3. **Cooldowns**: 15-minute selection cooldown and 24-hour success cooldown enforce rotation.
4. **Active User Demand**: Operator search vectors (`user_searches`) and profile preferences (`preferred_roles`, `preferred_locations`) apply dynamic prioritization boosts.
5. **Variant Quality Telemetry & Penalization**:
   - `UNTRIED`: Queued immediately for exploration.
   - `UNKNOWN` / Incomplete Telemetry: 1-day retry cooldown.
   - `NO_INVENTORY` (0 jobs returned): 7-day penalty cooldown.
   - `NO_FRESHER` (inventory returned, 0 fresher eligible): 7-day penalty cooldown.
   - `PRODUCTIVE`: Evaluated using Beta-smoothed fresher utility ($\alpha=1, \beta=4$, prior $0.20$); re-enters exploration queue after 30 days.
6. **Adaptive Retrieval Scope**: Fail-closed drought detection broadens granular Bangalore locations (`LOC-BLR-*` except canonical `LOC-BLR-001`) to canonical `"Bengaluru"` only when **all** candidate variants are verified `NO_INVENTORY`. Any variant with `UNKNOWN`, incomplete, or `NO_FRESHER` telemetry blocks broadening.
7. **Deterministic Fallback**: When all variants are under active penalty, the selector chooses the oldest attempt (`min(id)`), ensuring deterministic rotation without variant starvation.

## 005.8 Search Cycle Orchestration

To prevent infinite loops and memory bloat in n8n, 005.8 introduced a **Server-Authoritative Cycle Budget**.

### Budget Rules
- **Server-Owned:** The budget limit (`CYCLE_BUDGET`) is defined in the FastAPI configuration.
- **Correlation Identity:** n8n supplies a `cycle_id` (max 64 chars). Using the same `cycle_id` shares the identical budget namespace.
- **Exactly One Slot:** One slot in the `search_cycle_usage` budget strictly equals one persisted `search_execution` claim.
- **Atomic Reservation:** The reservation is incremented using an atomic PostgreSQL `ON CONFLICT DO UPDATE` query immediately before inserting the execution claim, within the same SQLAlchemy savepoint.
- **Failure Consumption:** Failures (Routing failure, Provider 502, Quota race) explicitly *consume* the cycle slot to prevent silent retries and infinite orchestration looping.
- **No Provider Fallback:** If a provider fails, the execution is marked `failed`. The same execution is never retried; n8n requests the next eligible candidate.

## Abandoned Claim Recovery

The search selection engine actively recovers orphaned execution claims on every selection cycle:
- **Scope**: Reclaims both stale `selected` claims (`status = 'selected' AND selected_at < :threshold`) and stale `started` claims (`status = 'started' AND COALESCE(started_at, selected_at) < :threshold`).
- **Lease Boundary**: Governed by `ABANDONED_CLAIM_MINUTES = 15`. Claims inactive beyond this 15-minute lease boundary are treated as expired leases rather than assumed dead workers.
- **Reclamation Transition**: Abandoned claims are transitioned to `status = 'failed'` with `error_message = 'abandoned claim'` and completed timestamp recorded, freeing candidates for subsequent rotation without orphan deadlocks.
