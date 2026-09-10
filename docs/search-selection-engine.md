# Search Selection Engine (005.7 / 005.8)

## Overview

The Search Selection Engine governs exactly *what* JobPilot searches for, *who* performs the search, and *how many* searches occur per run.

The architecture is explicitly separated:
- **005.6:** Provider quota authority and execution.
- **005.7:** Candidate selection (deterministic).
- **005.7B:** Provider routing intelligence.
- **005.8:** Bounded cycle orchestration.

## 005.7 Candidate Selection

Candidates are generated dynamically from the active taxonomy bounds.
They are scored based on:
1. Base Priority
2. Location Tier
3. Freshness History (24h success cooldown, 15m selected cooldown)

## 005.8 Search Cycle Orchestration

To prevent infinite loops and memory bloat in n8n, 005.8 introduced a **Server-Authoritative Cycle Budget**.

### Budget Rules
- **Server-Owned:** The budget limit (`CYCLE_BUDGET`) is defined in the FastAPI configuration.
- **Correlation Identity:** n8n supplies a `cycle_id` (max 64 chars). Using the same `cycle_id` shares the identical budget namespace.
- **Exactly One Slot:** One slot in the `search_cycle_usage` budget strictly equals one persisted `search_execution` claim.
- **Atomic Reservation:** The reservation is incremented using an atomic PostgreSQL `ON CONFLICT DO UPDATE` query immediately before inserting the execution claim, within the same SQLAlchemy savepoint.
- **Failure Consumption:** Failures (Routing failure, Provider 502, Quota race) explicitly *consume* the cycle slot to prevent silent retries and infinite orchestration looping.
- **No Provider Fallback:** If a provider fails, the execution is marked `failed`. The same execution is never retried; n8n requests the next eligible candidate.

### Known Limitations
- **Stale Started Recovery:** A catastrophic DB/Server failure after the `selected -> started` transition may leave the execution indefinitely stuck in `started`. The abandonment cleanup currently only cleans stale `selected` claims. Stale-started recovery remains deferred.
