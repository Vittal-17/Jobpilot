# Production Deployment Contract (005.9.1)

## Production Architecture

The production architecture for JobPilot is designed to run securely on a constrained ARM64 VPS (e.g. Oracle Cloud 2 vCPU, 10.6 GiB RAM).

```
Internet
  ↓ TCP 80/443
Caddy (Reverse Proxy, TLS)
  ↓ [frontend Docker network]
n8n (Orchestration)
  ↓ [backend Docker network]
FastAPI (Business Logic)
  ↓ [backend Docker network]
PostgreSQL (Source of Truth)
```

### Network Boundaries
- **Frontend Network (`jobpilot_frontend`)**: Connects Caddy to n8n.
- **Backend Network (`jobpilot_backend`)**: Connects n8n to FastAPI, and FastAPI to PostgreSQL.
- **Port Publication**: Only Caddy exposes host ports (80 and 443). FastAPI, PostgreSQL, and n8n have **no direct host port publication**.

## ARM64 Requirement
The target production VPS runs an ARM64/AArch64 processor (Neoverse-N1).
- All images (`docker.n8n.io/n8nio/n8n:2.38.1`, `postgres:16-alpine`, `caddy:2.8-alpine`, and `python:3.12-slim-bookworm`) have been validated for `linux/arm64` architecture support.
- Python dependencies are locked with `pip-compile` and built at runtime or CI time to ensure correct architecture wheels.

## Dev vs Production Compose

### Development (`docker-compose.yml`)
- Uses host bind mounts for live code reloading (`volumes: - ./backend:/app`).
- Exposes internal services directly to host (`8000` for FastAPI, `5432` for PostgreSQL, `5678` for n8n).
- Builds images implicitly or relies on `sh -c "pip install..."` entrypoints.

### Production (`docker-compose.production.yml`)
- Explicit deterministic image builds with `Dockerfile.production` containing copied application source. No bind mounts.
- Locked dependency versions.
- Strict network separation (no exposed ports except Caddy).
- Resource constraints configured in `deploy.resources.limits`.
- Docker json-file logging limits (max-size 10m, max-file 3) to prevent disk exhaustion.

## Persistent Volumes
Data is stored securely on Docker managed volumes with explicit stable names. Avoid `docker compose down -v` in production!

- `jobpilot_postgres_data`: Database files.
- `jobpilot_n8n_data`: Orchestrator state and configurations.
- `jobpilot_caddy_data`: SSL certificates and Caddy persistence.
- `jobpilot_caddy_config`: Caddy configurations.

## Production Environment Variables
Do not reuse `.env` from development. Use `.env.example` as a template for production.

**VPS-Only Secrets (Never commit):**
- `POSTGRES_PASSWORD`, `N8N_DB_PASSWORD`
- `API_SECRET_KEY` (FastAPI <-> n8n trust)
- `N8N_ENCRYPTION_KEY` (Must be high-entropy, secures n8n credentials)
- Provider keys (`ADZUNA_APP_ID`, `JOOBLE_API_KEY`)

**Configuration:**
- `ENVIRONMENT=production`
- `DOMAIN` (Target URL for Caddy and n8n webhooks)

## First Deployment Prerequisites
1. ARM64 capable Docker environment.
2. A populated `.env` file generated from `.env.example` with strong generated secrets.
3. Domain configured in DNS pointing to the VPS.

## No Automatic n8n Workflow Import
Production n8n container purposefully **does not** automatically import workflows from the repository. This protects production state from being inadvertently overwritten during standard container restarts.

## Migration Service Contract
Production database migrations are not automatically executed on container start to ensure safe multi-node rollouts or isolated testing.
Use the dedicated `migration` profile to run database schema upgrades:
```bash
docker compose -f docker-compose.production.yml --profile tools run --rm migration
```
This safely runs `alembic upgrade head` inside the fully loaded production image context.

## Disk & Log Retention Assumptions
- VPS has a ~19 GiB root filesystem and ~15 GiB secondary.
- Log rotation is forced at the Docker daemon level (`max-size: 10m`, `max-file: 3` per service).
- n8n is configured to prune executions older than 168 hours to minimize local database bloat (`EXECUTIONS_DATA_PRUNE=true`).

## Dependency Locking
Dependencies are locked to exact versions using `pip-tools`.
To update the locks, use a Python 3.12 environment (preferably matching production):
```bash
docker run --rm -v $(pwd)/backend:/backend python:3.12-slim sh -c "pip install pip-tools && pip-compile /backend/requirements.txt --output-file /backend/requirements.lock"
```
Do not hand-edit `requirements.lock`.
