#!/usr/bin/env python3
import argparse
import re
import os
import sys
import shutil
import subprocess
import json
import time
from datetime import datetime, timezone

# 1. EXACT RELEASE IDENTITY
SHA_REGEX = re.compile(r"^[0-9a-f]{40}$")
MIN_FREE_SPACE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
METADATA_FILE = ".current_release.json"
COMPOSE_FILE = "docker-compose.production.yml"
IMAGE_PREFIX = "jobpilot-fastapi"

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

def fail(message):
    eprint(f"[ERROR] {message}")
    sys.exit(1)

def log(message):
    print(f"[INFO] {message}")

def validate_sha(sha):
    if not sha:
        fail("No SHA provided. A 40-character Git commit SHA is required.")
    if sha.lower() == "latest":
        fail("'latest' is not a valid production release identity.")
    if not SHA_REGEX.match(sha):
        fail(f"Invalid SHA: {sha}. Must be exactly 40 lowercase hexadecimal characters.")
    return sha

def validate_environment():
    log("Validating environment...")
    # Check docker
    if not shutil.which("docker"):
        fail("Docker is not installed or not in PATH.")

    # Check compose file
    if not os.path.exists(COMPOSE_FILE):
        fail(f"Production compose file '{COMPOSE_FILE}' not found.")

    # Check disk space (configurable via MIN_FREE_SPACE_BYTES or ENV)
    min_space = int(os.environ.get("MIN_FREE_SPACE_BYTES", MIN_FREE_SPACE_BYTES))
    free_space = shutil.disk_usage(".").free
    if free_space < min_space:
        fail(f"Insufficient disk space. Required: {min_space} bytes, Available: {free_space} bytes.")

    # Check render
    res = subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "config"],
        capture_output=True,
        text=True
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

def validate_artifact(sha):
    log(f"Validating target artifact for SHA: {sha}...")
    image_name = f"{IMAGE_PREFIX}:{sha}"
    res = subprocess.run(
        ["docker", "image", "inspect", image_name],
        capture_output=True,
        text=True
    )
    if res.returncode != 0:
        fail(f"Target image '{image_name}' not found locally. Please build or pull it before deploying.")

    try:
        inspect_data = json.loads(res.stdout)
        arch = inspect_data[0].get("Architecture")
        if arch != "arm64":
            fail_msg = f"ARM64 MISMATCH: Image '{image_name}' has architecture '{arch}', not 'arm64'."
            if os.environ.get("ALLOW_ARCH_MISMATCH") == "true":
                eprint(f"[WARNING] {fail_msg} Proceeding due to ALLOW_ARCH_MISMATCH=true.")
            else:
                fail(fail_msg)
    except Exception as e:
        fail(f"Failed to inspect image: {e}")

    return image_name

def record_metadata(sha, image_name, prev_sha):
    log("Recording intended release metadata...")
    metadata = {
        "release_sha": sha,
        "image_reference": image_name,
        "deployment_timestamp": datetime.now(timezone.utc).isoformat(),
        "previous_release_sha": prev_sha
    }
    tmp_file = ".deploy_pending.json.tmp"
    with open(tmp_file, "w") as f:
        json.dump(metadata, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.rename(tmp_file, ".deploy_pending.json")
    return metadata

def start_deployment(sha):
    log("Starting controlled deployment...")
    env = os.environ.copy()
    # We already verified it renders, but we run up -d
    res = subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "up", "-d", "--no-build"],
        env=env
    )
    if res.returncode != 0:
        fail("docker compose up -d failed. Controlled rollback is NOT automated in this milestone.")

def wait_for_health():
    log("Waiting for FastAPI readiness...")
    # Get the container ID of the fastapi service
    # Assuming standard compose naming or we can use docker compose ps
    timeout = 60
    start = time.time()

    while time.time() - start < timeout:
        res = subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "ps", "--format", "json", "fastapi"],
            capture_output=True,
            text=True
        )
        if res.returncode == 0 and res.stdout.strip():
            try:
                # Could be multiple lines if scaled, take first
                containers = [json.loads(line) for line in res.stdout.strip().split("\n")]
                fastapi = containers[0]
                health = fastapi.get("Health", "")
                if health == "healthy":
                    log("FastAPI is healthy.")
                    return fastapi.get("ID") or fastapi.get("Name")
                elif health == "unhealthy":
                    fail("FastAPI became unhealthy during deployment. Inspect logs.")
            except Exception:
                pass
        time.sleep(2)

    fail("FastAPI healthcheck timed out after 60 seconds.")

def verify_release(sha, container_id):
    log(f"Verifying deployed release matches SHA {sha}...")
    # Inspect the running container to check its image and env
    res = subprocess.run(
        ["docker", "inspect", container_id],
        capture_output=True,
        text=True
    )
    if res.returncode != 0:
        fail(f"Could not inspect running container {container_id}")

    try:
        data = json.loads(res.stdout)[0]
        # Check Image Config
        image = data["Config"]["Image"]
        expected_suffix = f"{IMAGE_PREFIX}:{sha}"
        if image != expected_suffix and not image.endswith(f"/{expected_suffix}"):
            fail(f"Verification failed: Running image '{image}' does not match expected exact suffix '{expected_suffix}'.")

        # Check explicit env metadata if we want
        env_vars = data["Config"].get("Env", [])
        sha_env = [e for e in env_vars if e.startswith("APP_COMMIT_SHA=")]
        if not sha_env or sha_env[0].split("=")[1] != sha:
            fail(f"Verification failed: Container env APP_COMMIT_SHA does not match '{sha}'.")

        log("Release verification passed authoritatively.")
    except Exception as e:
        fail(f"Failed to verify release identity: {e}")

def main():
    parser = argparse.ArgumentParser(description="Exact-SHA Deployment Engine")
    parser.add_argument("--sha", required=True, help="Exact 40-character Git commit SHA to deploy")
    args = parser.parse_args()

    # Preflight
    sha = validate_sha(args.sha)
    os.environ["APP_COMMIT_SHA"] = sha  # Export for docker-compose
    validate_environment()

    # Idempotency check
    current = get_current_release()
    if current.get("release_sha") == sha:
        log(f"Release {sha} is already the current recorded release.")
        # We can still ensure it is healthy and matches
        try:
            cid = wait_for_health()
            verify_release(sha, cid)
            log("Idempotent deployment successful: state is already desired.")
            sys.exit(0)
        except SystemExit:
            # If verification failed, we proceed to deploy it to fix the drift
            log("Idempotent verification failed (drift detected). Proceeding with deployment...")

    # Validate Artifact
    image_name = validate_artifact(sha)

    # Record intent
    metadata = record_metadata(sha, image_name, current.get("release_sha"))

    # Rollout
    start_deployment(sha)

    # Verify
    cid = wait_for_health()
    verify_release(sha, cid)

    # Commit metadata
    os.rename(".deploy_pending.json", METADATA_FILE)
    log(f"SUCCESS: Release {sha} deployed and verified.")

if __name__ == "__main__":
    main()
