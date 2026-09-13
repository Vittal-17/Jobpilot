#!/usr/bin/env python3
import argparse
import re
import os
import sys
import shutil
import subprocess
import json
import time
import hashlib
from datetime import datetime, timezone
from enum import Enum

# 1. EXACT RELEASE IDENTITY & LIMITS
SHA_REGEX = re.compile(r"^[0-9a-f]{40}$")
MIN_FREE_SPACE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
METADATA_FILE = ".current_release.json"
PENDING_FILE = ".deploy_pending.json"
COMPOSE_FILE = "docker-compose.production.yml"
CANONICAL_IMAGE = "ghcr.io/vittal-17/jobpilot-fastapi"
LOCK_FILE = ".deploy.lock"
BACKUP_DIR = "backups"

class DeployPhase(str, Enum):
    PREPARED = "prepared"
    BACKUP_CREATED = "backup_created"
    BACKUP_VERIFIED = "backup_verified"
    RESTORE_VERIFIED = "restore_verified"
    MIGRATION_STARTED = "migration_started"
    MIGRATION_SUCCEEDED = "migration_succeeded"
    ROLLOUT_STARTED = "rollout_started"
    ROLLOUT_SUCCEEDED = "rollout_succeeded"
    FAILED = "failed"

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

def fail(message, metadata=None):
    eprint(f"[ERROR] {message}")
    if metadata:
        update_phase(metadata, DeployPhase.FAILED)
    release_lock()
    sys.exit(1)

def log(message):
    print(f"[INFO] {message}")

import fcntl

lock_fd = None

def acquire_lock():
    global lock_fd
    lock_fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Informational PID writing, clear file first
        os.ftruncate(lock_fd, 0)
        os.write(lock_fd, str(os.getpid()).encode())
    except (IOError, BlockingIOError):
        fail("Another deployment is actively running (lock acquired by another process).")

def release_lock():
    global lock_fd
    if lock_fd is not None:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        except OSError:
            pass
        lock_fd = None

def update_phase(metadata, phase):
    metadata["phase"] = phase.value
    tmp_file = f"{PENDING_FILE}.tmp"
    with open(tmp_file, "w") as f:
        json.dump(metadata, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.rename(tmp_file, PENDING_FILE)
    log(f"Phase transitioned to: {phase.value}")

def validate_sha(sha):
    if not sha:
        fail("No SHA provided. A 40-character Git commit SHA is required.")
    if sha.lower() == "latest":
        fail("'latest' is not a valid production release identity.")
    if not SHA_REGEX.match(sha):
        fail(f"Invalid SHA: {sha}. Must be exactly 40 lowercase hexadecimal characters.")
    return sha


def is_valid_acme_domain(domain: str) -> bool:
    if not domain or len(domain) > 253:
        return False
    # Reject URL schemes, ports, paths, whitespace, query, fragments
    if any(c in domain for c in (':', '/', ' ', '?', '#')):
        return False

    labels = domain.split('.')
    if len(labels) < 2:
        return False

    label_regex = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")
    for label in labels:
        if not label_regex.match(label):
            return False

    # Reject pure IPv4
    if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", domain):
        return False

    return True

def load_production_env(filepath="/opt/jobpilot/shared/.env") -> dict:
    if not os.path.lexists(filepath):
        fail(f"Required shared environment file '{filepath}' not found.")

    if os.path.islink(filepath):
        fail(f"'{filepath}' is a symlink, which is not permitted.")

    st = os.stat(filepath)
    import stat
    if not stat.S_ISREG(st.st_mode):
        fail(f"'{filepath}' exists but is not a regular file.")

    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        fail(f"Unsafe '{filepath}' permissions. File must not be group or world accessible. Run: chmod 600 {filepath}")

    env_data = {}
    key_regex = re.compile(r'^[A-Z_][A-Z0-9_]*$')

    with open(filepath, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            clean_line = line.strip()
            if not clean_line or clean_line.startswith('#'):
                continue

            if '=' not in clean_line:
                fail(f"Malformed assignment in '{filepath}' on line {line_num} (missing '=').")

            key, val = clean_line.split('=', 1)
            key = key.strip()
            val = val.strip()

            if not key_regex.match(key):
                fail(f"Invalid variable name '{key}' in '{filepath}' on line {line_num}.")

            if key in env_data:
                fail(f"Duplicate key '{key}' found in '{filepath}' on line {line_num}.")

            # Handle quoting
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                if len(val) >= 2:
                    val = val[1:-1]

            env_data[key] = val

    return env_data

def resolve_effective_config(env_data: dict, process_env: dict) -> dict:
    config = {}
    config.update(env_data)
    # Process environment overrides .env (Compose semantics)
    for k, v in process_env.items():
        config[k] = v
    return config

def validate_secrets(config: dict):
    log("Validating production configuration values...")
    required_keys = [
        "API_SECRET_KEY", "POSTGRES_PASSWORD", "N8N_DB_PASSWORD",
        "N8N_ENCRYPTION_KEY", "CADDY_ADMIN_HASH", "CADDY_ADMIN_USER",
        "DOMAIN", "ACME_EMAIL", "POSTGRES_DB", "POSTGRES_USER",
        "N8N_DB_NAME", "N8N_DB_USER"
    ]
    for key in required_keys:
        val = config.get(key, "").strip()
        if not val:
            fail(f"Required secret {key} is missing or empty.")
        if "__REPLACE_WITH" in val:
            fail(f"Required secret {key} contains a placeholder value.")

    optional_keys = [
        "ADZUNA_APP_ID", "ADZUNA_APP_KEY", "JOOBLE_API_KEY",
        "BACKUP_ENCRYPTION_KEY", "BACKUP_S3_ACCESS_KEY", "BACKUP_S3_SECRET_KEY"
    ]
    for key in optional_keys:
        val = config.get(key, "").strip()
        if "__REPLACE_WITH" in val:
            fail(f"Optional secret {key} contains a placeholder value.")

def validate_environment(config):
    log("Validating environment...")

    env_file = "/opt/jobpilot/shared/.env"
    if os.path.exists(env_file):
        import stat
        st = os.stat(env_file)
        if not stat.S_ISREG(st.st_mode):
            fail(f"'{env_file}' exists but is not a regular file.")
        if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            fail(f"Unsafe '{env_file}' permissions. File must not be group or world accessible. Run: chmod 600 {env_file}")

    if not shutil.which("docker"):
        fail("Docker is not installed or not in PATH.")
    if not os.path.exists(COMPOSE_FILE):
        fail(f"Production compose file '{COMPOSE_FILE}' not found.")

    domain = config.get("DOMAIN", "")
    if not is_valid_acme_domain(domain):
        fail(f"DOMAIN '{domain}' is not a valid FQDN for ACME TLS.")

    min_space = int(os.environ.get("MIN_FREE_SPACE_BYTES", MIN_FREE_SPACE_BYTES))
    free_space = shutil.disk_usage(".").free
    if free_space < min_space:
        fail(f"Insufficient disk space. Required: {min_space} bytes, Available: {free_space} bytes.")

    res = subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "config"],
        capture_output=True, text=True
    )
    if res.returncode != 0:
        fail(f"docker compose config failed to render:\n{res.stderr}")

def get_current_release():
    if os.path.exists(METADATA_FILE):
        try:
            with open(METADATA_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            eprint(f"[WARNING] Could not parse existing metadata: {e}")
    return {}

def validate_artifact(sha, expected_digest=None):
    log(f"Validating target artifact for SHA: {sha}...")
    image_name = f"{CANONICAL_IMAGE}:{sha}"

    log(f"Pulling canonical image reference: {image_name}")
    pull_res = subprocess.run(["docker", "pull", image_name], capture_output=True, text=True)
    if pull_res.returncode != 0:
        fail(
            f"Failed to pull canonical image '{image_name}':\n"
            f"{pull_res.stderr}"
        )

    res = subprocess.run(
        ["docker", "image", "inspect", image_name],
        capture_output=True, text=True
    )
    if res.returncode != 0:
        fail(f"Target image '{image_name}' not found locally after pull.")

    try:
        inspect_data = json.loads(res.stdout)
        data = inspect_data[0]

        arch = data.get("Architecture")
        if arch != "arm64":
            fail_msg = f"ARM64 MISMATCH: Image '{image_name}' has architecture '{arch}', not 'arm64'."
            if os.environ.get("ALLOW_ARCH_MISMATCH") == "true":
                eprint(f"[WARNING] {fail_msg} Proceeding due to ALLOW_ARCH_MISMATCH=true.")
            else:
                fail(fail_msg)

        env_vars = data.get("Config", {}).get("Env", [])
        sha_env = [e for e in env_vars if e.startswith("APP_COMMIT_SHA=")]
        if not sha_env or sha_env[0].split("=")[1] != sha:
            fail(f"Container configuration identity verification failed: APP_COMMIT_SHA does not equal '{sha}'.")

        if expected_digest:
            repo_digests = data.get("RepoDigests", [])
            if not any(expected_digest in rd for rd in repo_digests):
                fail(f"Image digest mismatch. Expected '{expected_digest}', found: {repo_digests}")
            log("Image digest verified successfully.")

    except Exception as e:
        fail(f"Failed to inspect image: {e}")

    return image_name


def init_metadata(sha, image_name, prev_sha):
    log("Initializing deployment metadata...")
    metadata = {
        "release_sha": sha,
        "image_reference": image_name,
        "deployment_timestamp": datetime.now(timezone.utc).isoformat(),
        "previous_release_sha": prev_sha,
        "phase": DeployPhase.PREPARED.value
    }
    update_phase(metadata, DeployPhase.PREPARED)
    return metadata

def check_db_ready():
    log("Ensuring database is running before operations...")
    res = subprocess.run(["docker", "compose", "-f", COMPOSE_FILE, "ps", "--format", "json", "db"], capture_output=True, text=True)
    if "healthy" not in res.stdout:
        log("DB not healthy or running. Starting DB...")
        subprocess.run(["docker", "compose", "-f", COMPOSE_FILE, "up", "-d", "db"], check=True)
        # Wait for health
        for _ in range(30):
            res = subprocess.run(["docker", "compose", "-f", COMPOSE_FILE, "ps", "--format", "json", "db"], capture_output=True, text=True)
            if "healthy" in res.stdout:
                return
            time.sleep(2)
        fail("Database failed to become healthy.")

def create_backup(sha, metadata, config):
    log("Creating pre-migration database backup...")
    os.makedirs(BACKUP_DIR, mode=0o700, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    filename = f"backup_{sha}_{timestamp}.dump"
    filepath = os.path.join(BACKUP_DIR, filename)

    try:
        fd = os.open(filepath, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        fail(f"Backup file collision: {filepath} already exists. Refusing to overwrite.", metadata)

    run_env = os.environ.copy()
    run_env["PGPASSWORD"] = config.get("POSTGRES_PASSWORD", "")
    cmd = [
        "docker", "compose", "-f", COMPOSE_FILE,
        "exec", "-T", "-e", "PGPASSWORD",
        "db", "pg_dump", "-U", config["POSTGRES_USER"], "-d", config["POSTGRES_DB"], "-Fc"
    ]
    with open(fd, "wb") as f:
        res = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, env=run_env)
        if res.returncode != 0:
            try: os.remove(filepath)
            except OSError: pass
            fail(f"Backup pg_dump failed: {res.stderr.decode()}", metadata)

    if os.path.getsize(filepath) == 0:
        try: os.remove(filepath)
        except OSError: pass
        fail("Backup file is empty!", metadata)

    # Checksum
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for block in iter(lambda: f.read(4096), b""):
            sha256.update(block)
    checksum = sha256.hexdigest()

    # Integrity list check
    cmd_list = [
        "docker", "compose", "-f", COMPOSE_FILE,
        "exec", "-T", "db", "pg_restore", "-l"
    ]
    with open(filepath, "rb") as f:
        res = subprocess.run(cmd_list, stdin=f, capture_output=True)
    if res.returncode != 0:
        fail(f"Archive integrity list verification failed: {res.stderr.decode()}", metadata)

    metadata["backup"] = {
        "file": filepath,
        "checksum": checksum,
        "format": "custom",
        "timestamp": timestamp,
        "status": "verified"
    }
    update_phase(metadata, DeployPhase.BACKUP_VERIFIED)

def verify_restore(metadata):
    log("Verifying restore in isolated container...")
    container_name = f"jobpilot_verify_db_{int(time.time())}"
    filepath = metadata["backup"]["file"]

    if os.environ.get("SKIP_RESTORE_VERIFY") == "true":
        log("Skipping restore verify due to SKIP_RESTORE_VERIFY=true.")
        metadata["backup"]["restore_verified"] = True
        update_phase(metadata, DeployPhase.RESTORE_VERIFIED)
        return

    run_env = os.environ.copy()
    run_env["POSTGRES_PASSWORD"] = "verify"
    run_env["POSTGRES_DB"] = "verify"
    run_env["PGPASSWORD"] = "verify"

    subprocess.run([
        "docker", "run", "-d", "--name", container_name,
        "-e", "POSTGRES_PASSWORD",
        "-e", "POSTGRES_DB",
        "postgres:16-alpine"
    ], check=True, stdout=subprocess.DEVNULL, env=run_env)

    try:
        ready = False
        for _ in range(15):
            res = subprocess.run([
                "docker", "exec", "-e", "PGPASSWORD", container_name, "pg_isready", "-U", "postgres", "-d", "verify"
            ], capture_output=True, env=run_env)
            if res.returncode == 0:
                ready = True
                break
            time.sleep(2)
        if not ready:
            fail("Isolated verification DB failed to start.", metadata)

        # Restore without privileges/owner to avoid role errors in isolated DB
        cmd_restore = [
            "docker", "exec", "-i", "-e", "PGPASSWORD", container_name,
            "pg_restore", "-U", "postgres", "-d", "verify", "--no-owner", "--no-privileges"
        ]
        with open(filepath, "rb") as f:
            res = subprocess.run(cmd_restore, stdin=f, capture_output=True, env=run_env)
        # Any non-zero return code is a hard failure.
        if res.returncode != 0:
            fail(f"Restore verification failed: {res.stderr.decode()}", metadata)

        metadata["backup"]["restore_verified"] = True
        update_phase(metadata, DeployPhase.RESTORE_VERIFIED)
    finally:
        subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def extract_alembic_revs(output):
    revs = set()
    for line in output.split('\n'):
        if line.startswith("INFO"):
            continue
        match = re.search(r"([a-f0-9]{10,})", line)
        if match:
            revs.add(match.group(1))
    return revs

def execute_migration(metadata, config):
    log("Executing DB migration...")

    # Determine expected heads
    res_heads = subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "run", "--rm", "--no-build", "migration", "alembic", "heads"],
        capture_output=True, text=True
    )
    if res_heads.returncode != 0:
        fail("Failed to determine expected Alembic heads.", metadata)

    expected_heads = extract_alembic_revs(res_heads.stdout)
    if not expected_heads:
        fail(f"Could not parse expected Alembic heads from output: {res_heads.stdout}", metadata)

    update_phase(metadata, DeployPhase.MIGRATION_STARTED)

    res = subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "run", "--rm", "--no-build", "migration", "alembic", "upgrade", "head"],
        capture_output=True, text=True
    )
    if res.returncode != 0:
        fail(f"Migration failed:\n{res.stderr}\n{res.stdout}\nStop. Preserving backup. Do not deploy.", metadata)

    res_current = subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "run", "--rm", "--no-build", "migration", "alembic", "current"],
        capture_output=True, text=True
    )
    if res_current.returncode != 0:
        fail("Failed to verify Alembic head after migration.", metadata)

    current_heads = extract_alembic_revs(res_current.stdout)
    if not current_heads:
        fail(f"Could not parse current Alembic revision from output: {res_current.stdout}", metadata)

    if current_heads != expected_heads:
        fail(f"Alembic head mismatch. Expected: {expected_heads}, Current: {current_heads}", metadata)

    # DB connectivity check
    run_env = os.environ.copy()
    run_env["PGPASSWORD"] = config.get("POSTGRES_PASSWORD", "")
    cmd_check = [
        "docker", "compose", "-f", COMPOSE_FILE,
        "exec", "-T", "-e", "PGPASSWORD",
        "db", "psql", "-U", config["POSTGRES_USER"], "-d", config["POSTGRES_DB"], "-c", "SELECT 1;"
    ]
    res_db = subprocess.run(cmd_check, capture_output=True, text=True, env=run_env)
    if res_db.returncode != 0:
        fail("Post-migration database connectivity test failed.", metadata)

    update_phase(metadata, DeployPhase.MIGRATION_SUCCEEDED)

def start_deployment(sha, metadata):
    log("Starting controlled deployment...")
    update_phase(metadata, DeployPhase.ROLLOUT_STARTED)
    res = subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "up", "-d", "--no-build"]
    )
    if res.returncode != 0:
        fail("docker compose up -d failed. Controlled rollback is NOT automated in this milestone.", metadata)

def wait_for_health(metadata):
    log("Waiting for FastAPI readiness...")
    timeout = 60
    start = time.time()

    while time.time() - start < timeout:
        res = subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "ps", "--format", "json", "fastapi"],
            capture_output=True, text=True
        )
        if res.returncode == 0 and res.stdout.strip():
            try:
                containers = [json.loads(line) for line in res.stdout.strip().split("\n")]
                fastapi = containers[0]
                health = fastapi.get("Health", "")
                if health == "healthy":
                    log("FastAPI is healthy.")
                    return fastapi.get("ID") or fastapi.get("Name")
                elif health == "unhealthy":
                    fail("FastAPI became unhealthy during deployment. Inspect logs.", metadata)
            except Exception:
                pass
        time.sleep(2)

    fail("FastAPI healthcheck timed out after 60 seconds.", metadata)

def verify_release(sha, container_id, metadata):
    log(f"Verifying deployed release matches SHA {sha}...")
    res = subprocess.run(
        ["docker", "inspect", container_id],
        capture_output=True, text=True
    )
    if res.returncode != 0:
        fail(f"Could not inspect running container {container_id}", metadata)

    try:
        data = json.loads(res.stdout)[0]
        image = data["Config"]["Image"]
        expected_suffix = f"{CANONICAL_IMAGE}:{sha}"
        if image != expected_suffix and not image.endswith(f"/{expected_suffix}"):
            fail(f"Verification failed: Running image '{image}' does not match expected exact suffix '{expected_suffix}'.", metadata)

        env_vars = data["Config"].get("Env", [])
        sha_env = [e for e in env_vars if e.startswith("APP_COMMIT_SHA=")]
        if not sha_env or sha_env[0].split("=")[1] != sha:
            fail(f"Verification failed: Container env APP_COMMIT_SHA does not match '{sha}'.", metadata)

        log("Release verification passed authoritatively.")
        update_phase(metadata, DeployPhase.ROLLOUT_SUCCEEDED)
    except SystemExit:
        raise
    except Exception as e:
        fail(f"Failed to verify release identity: {e}", metadata)

def main():
    parser = argparse.ArgumentParser(description="Exact-SHA Deployment Engine with Migration Safety")
    parser.add_argument("--sha", required=True, help="Exact 40-character Git commit SHA to deploy")
    args = parser.parse_args()

    acquire_lock()
    try:
        sha = validate_sha(args.sha)
        os.environ["APP_COMMIT_SHA"] = sha

        env_data = load_production_env()
        config = resolve_effective_config(env_data, os.environ)

        validate_secrets(config)

        validate_environment(config)

        current = get_current_release()
        if current.get("release_sha") == sha and current.get("phase") == DeployPhase.ROLLOUT_SUCCEEDED.value:
            log(f"Release {sha} is already the current successful release.")
            try:
                cid = wait_for_health(current)
                verify_release(sha, cid, current)
                log("Idempotent deployment successful: state is already desired.")
                sys.exit(0)
            except SystemExit:
                log("Idempotent verification failed (drift detected). Proceeding with deployment...")

        expected_digest = os.environ.get("EXPECTED_IMAGE_DIGEST")

        # Load from image_digest.txt if available in release directory
        release_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        digest_file = os.path.join(release_dir, "image_digest.txt")
        if os.path.exists(digest_file):
            with open(digest_file) as df:
                expected_digest = df.read().strip()

        image_name = validate_artifact(sha, expected_digest)
        metadata = init_metadata(sha, image_name, current.get("release_sha"))

        check_db_ready()
        create_backup(sha, metadata, config)
        verify_restore(metadata)

        execute_migration(metadata, config)

        start_deployment(sha, metadata)

        cid = wait_for_health(metadata)
        verify_release(sha, cid, metadata)

        os.rename(PENDING_FILE, METADATA_FILE)
        log(f"SUCCESS: Release {sha} deployed and verified safely.")
    finally:
        release_lock()

if __name__ == "__main__":
    main()
