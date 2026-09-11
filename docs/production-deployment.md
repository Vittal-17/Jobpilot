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


## Edge Security & Proxy Trust (005.9.4)

### Caddy Ingress & TLS (ACME)
- **Port Exposure**: Only TCP 80 and TCP 443 are publicly reachable.
- **TLS**: Caddy negotiates TLS via ACME (Let's Encrypt or ZeroSSL). The `ACME_EMAIL` environment variable is required to register the ACME account and receive expiration/revocation notices.
- **HSTS**: Caddy enforces `Strict-Transport-Security` (`max-age=31536000; includeSubDomains`) alongside `X-Content-Type-Options` and `X-Frame-Options` on the entire domain to block protocol downgrade attacks.
- **DNS Requirements**: The application structurally validates the syntax/shape of `DOMAIN` to ensure it conforms to DNS naming rules suitable for ACME. DNS correctness (valid A/AAAA records pointing to the VPS) remains an external deployment prerequisite.
- **VCN Firewall**: The Oracle VCN firewall must explicitly allow ingress TCP traffic on ports 80 and 443. Ensure no IPv6 pathways (AAAA records) bypass intended IPv4-only firewall rules.

### n8n UI Protection (First-Boot Hijacking Prevention)
- **First-Boot Vulnerability**: n8n (v1.0+) mandates User Management on the first visit. If exposed publicly, an attacker could hijack the Owner account.
- **Mitigation**: Caddy restricts access using a `basic_auth` block for all paths except `/webhook/*` and `/webhook-test/*`.
- **Configuration**: The `CADDY_ADMIN_USER` and `CADDY_ADMIN_HASH` variables in `.env` secure the UI. Generate the bcrypt hash using an interactive prompt to prevent shell history leakage:
  ```bash
  docker run -it --rm caddy:2.8-alpine caddy hash-password
  ```
  *Never store the plaintext password in Git or your `.env` file.*

### Proxy Trust Headers
- **X-Forwarded-For Protection**: n8n must know Caddy is a trusted proxy to correctly resolve client IPs (essential for webhook IP restrictions and rate limiting). This is strictly mapped via `N8N_PROXY_HOPS=1`.
- **Caddy Edge Semantics**: The client connects directly to Caddy. By default, Caddy does NOT trust incoming `X-Forwarded-*` values from the internet. Caddy sets and sanitizes its own proxy metadata. An attacker-supplied `X-Forwarded-For` header is not treated as authoritative. Consequently, n8n receives Caddy's sanitized proxy metadata, ensuring the true TCP socket IP is represented.
- **Why N8N_PROXY_HOPS=1?**: n8n uses Express.js under the hood. Setting `N8N_PROXY_HOPS=1` instructs Express that there is exactly *one* reverse-proxy hop (Caddy). Express safely evaluates the sanitized header chain provided by Caddy and correctly binds `req.ip`.
- **Note on Network Topology**: This configuration explicitly requires that Caddy is directly exposed to clients. There is currently no external CDN/load balancer/proxy in front of Caddy, so Caddy does not configure `trusted_proxies`. If a future Cloudflare or external load-balancer layer is introduced, Caddy's `trusted_proxies` configuration must be added, and `N8N_PROXY_HOPS` must be reevaluated. N8N_PROXY_HOPS is NOT interchangeable with arbitrary CIDR trust lists.

### Internal Isolation Invariants
- `n8n` port 5678 must remain private.
- `FastAPI` port 8000 must remain private.
- `PostgreSQL` port 5432 must remain private.
- Webhook callbacks are securely preserved because Caddy routes `/webhook/*` directly to n8n without basic auth.

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

### Secure File Permissions Contract
The `.env` file is a localized plaintext secret storage mechanism suitable for $0 infrastructure. The deployment operator **must** protect this file using Unix filesystem permissions.

### Configuration Loading & Precedence
The deployment pipeline requires no 3rd-party Python dependencies (like `python-dotenv`). It implements a strict, native parser that:
- Natively loads `.env` files matching the `KEY=value` or `KEY="value"` syntax.
- Completely ignores comments (`#`) and empty lines.
- Does **not** perform shell evaluation, parameter expansion (`$VAR`), or command substitution.
- Safely overlays process environment variables (process environment overrides `.env` values natively, matching Docker Compose semantics).

Because process variables override the `.env` file, CI/CD runners can safely inject environment credentials entirely in-memory without needing to write a `.env` file to disk. Manual `export` of variables by the operator is NOT required when a local `.env` file is present.
- **Location**: On the VPS deployment root.
- **Permissions**: `0600` is the normal recommended baseline, though stricter permissions such as `0400` are acceptable. The file must not be group or world accessible.
- **Ownership**: Must be owned by the user account responsible for deployment (e.g. `chown deploy_user:deploy_group .env`).
- **Placeholder Rejection**: The deployment engine strictly refuses to run if any required secret is missing, empty, or contains `__REPLACE_WITH`.

### Secret Lifecycle & Recovery
- **N8N_ENCRYPTION_KEY**: This is a persistent security-critical secret. It must remain stable. Encrypted credential data may become unusable if lost or changed, and recovery may require credential reconfiguration. Do not auto-regenerate. Do not rotate during ordinary deployment. Maintain a secure recovery copy outside Git.
- **Database Password Rotation**: Changing `POSTGRES_PASSWORD` in `.env` alone will NOT automatically rotate the already-initialized PostgreSQL superuser password on an existing data volume. Safe rotation requires a manual `ALTER USER ... WITH PASSWORD ...` execution against the live database, synchronized with the `.env` update.
- **Provider Secrets**: `ADZUNA_*` and `JOOBLE_*` credentials belong exclusively to FastAPI. They are safely decoupled from n8n and Caddy.

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

## CI/CD Pipeline (005.9.6)

### Architecture
The JobPilot CI/CD architecture is split into two independent workflows to protect secrets and ensure trusted deployments.

- **CI Pipeline:** Runs tests in an isolated GitHub Actions `ubuntu-latest` environment on all pushes and PRs to `master`. Uses a PostgreSQL 16 service container and tests against Python 3.12. It explicitly operates with minimal, read-only permissions and possesses no access to production credentials.
- **CD Pipeline (Image Publish):** A trusted build-and-publish job that triggers exclusively upon a successful CI run on the `master` branch. It utilizes GitHub's native `ubuntu-24.04-arm` runner to compile a native ARM64 image without QEMU emulation penalties.

### Exact SHA Identity
The deployment pipeline strongly forbids mutable tags like `latest`. The native ARM64 image is built and tagged precisely with its 40-character Git commit SHA, creating an immutable artifact linked deterministically to the repository state. The SHA is structurally validated before publishing.

### GHCR Package Visibility
Production images are published to the GitHub Container Registry (`ghcr.io`) using the scoped `GITHUB_TOKEN` automatically provided by GitHub Actions (requiring only `packages: write`).
**Note:** The first time an image is published, it may default to "Private" visibility. An operator must manually navigate to the repository's Package settings on GitHub and change the visibility of the `jobpilot-fastapi` package to **Public**. This permits the VPS deployment script to pull the image securely over HTTPS without requiring complex local credential management.

### No Deployment Implemented Yet
As of 005.9.6-B, automatic rollout to the VPS is intentionally deferred. The CD pipeline orchestrates the required ARM64 image publication only. Future milestones will implement SSH-based pull deployments executing `scripts/deploy.py` on the VPS.
