# n8n Orchestration Architecture

This document describes how JobPilot interacts with n8n for orchestration.

## Boundary Principles

JobPilot's architecture uses a strict, server-authoritative separation of concerns:
- **FastAPI / PostgreSQL:** Owns 100% of business logic, candidates, ranking, quota counting, state tracking, and cycle budgeting.
- **n8n:** Acts exclusively as a dumb task runner and scheduler.

n8n must never:
- Keep track of provider quotas.
- Determine which candidate to execute next.
- Retrying failed executions silently.
- Fall back to another provider.

## Orchestration Flow

**005.8: Bounded Search Cycle**

The orchestration runs via a bounded loop in n8n (`JP___Search_Cycle.json`).

1. **Trigger:** n8n starts (via Manual or Cron).
2. **Loop Node:** n8n enters a loop.
3. **Select Next:** n8n calls `POST /internal/select-next`, passing its `$execution.id` as `cycle_id`.
4. **Action Check:**
    - If `action == "stop"`, n8n breaks the loop. The server determines the stop condition (Budget Exhausted, No Eligible Candidates, Quota Exhausted).
    - If `action == "execute"`, n8n proceeds to execution.
5. **Execute:** n8n calls `POST /internal/search` with the provided `intent`.
6. **Continue On Fail:** The Execute HTTP node is configured to `Continue On Fail`. If the provider returns 502 (Bad Gateway), the loop proceeds to the next iteration. FastAPI inherently handles marking the execution as `failed` and places it on cooldown, so the next `select-next` call will return a new candidate safely.
