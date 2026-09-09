# JobPilot n8n Local Deployment Guide

This guide documents the persistent, self-hosted local n8n deployment for JobPilot.

## A. Starting n8n
Ensure you have a `.env` file populated using `.env.example` as a template.
Run:
```bash
docker compose up -d n8n
```
*(This will automatically ensure the PostgreSQL database and initialization step run first).*

## B. Stopping n8n
To stop n8n without destroying data:
```bash
docker compose stop n8n
```
To bring down the entire stack:
```bash
docker compose down
```

## C. Inspecting Status
Check the status of running containers:
```bash
docker compose ps
```

## D. Inspecting Logs
To view n8n logs and troubleshoot startup issues:
```bash
docker compose logs -f n8n
```

## E & F. Persistence & Volumes
All n8n state (workflows, credentials, execution history) is persisted in the Docker volume named `n8n_data`, which mounts internally to `/home/node/.n8n`. Data securely survives container restarts and host reboots.

## G & H. Database Connection & Isolation
n8n connects to the existing JobPilot PostgreSQL instance (`db` service in compose) via Docker's internal networking.
**Isolation:** n8n uses a strictly isolated database (`n8n_data`) and user (`n8n_user`). A one-shot `db-setup` container automatically initializes this on startup. n8n_user is explicitly revoked CONNECT privileges to the `jobpilot` application database and lacks superuser attributes.

## I. Secrets
Secrets must NEVER be committed. Populate them only in the `.env` file based on `.env.example`.
- **N8N_ENCRYPTION_KEY**: Encrypts all credentials in the n8n database.

## J. Regenerating the Encryption Key
**WARNING:** The `N8N_ENCRYPTION_KEY` is stable and ties your n8n database to its stored credentials. If you change or regenerate this key, **all previously saved credentials in n8n will be irrecoverably lost** and must be re-entered. Treat key rotation as a destructive migration event.

## K & L. Backup and Restore
- **Backup:**
  ```bash
  docker exec jobpilot-db-1 pg_dump -U n8n_user n8n_data > n8n_backup.sql
  # You MUST also backup your .env file containing the N8N_ENCRYPTION_KEY. This backup must be strictly protected and encrypted at rest.
  ```
- **Restore:**
  ```bash
  cat n8n_backup.sql | docker exec -i jobpilot-db-1 psql -U n8n_user -d n8n_data
  ```

## M. Upgrading n8n
The n8n version is pinned (e.g., `2.38.1`). To upgrade safely:
1. Review n8n release notes.
2. Back up the database and `.env`.
3. Update the image tag in `docker-compose.yml`.
4. Run `docker compose up -d n8n` to pull and recreate the container.
5. Verify health and workflows.

## N. Rollback
If an upgrade fails, revert the image tag in `docker-compose.yml` and run `docker compose up -d n8n`. If migrations were destructive, restore the database from the prior step's backup.

## O. Health Verification
Verify n8n is healthy by navigating to `http://localhost:5678` in your browser.

## P. Explicit Provider Separation
**n8n DOES NOT contain Adzuna or Jooble credentials.** These remain strictly managed by the FastAPI backend to enforce quota safety.

## Q. License Key
The deployment uses n8n Community Edition. If you possess a lifetime feature-unlock license key, you may activate it manually in the n8n UI under Settings. **DO NOT** commit the key into `.env` or source control.
