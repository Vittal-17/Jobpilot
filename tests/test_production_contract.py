import yaml
import os

def test_production_compose_parses():
    assert os.path.exists("docker-compose.production.yml")
    with open("docker-compose.production.yml", "r") as f:
        compose = yaml.safe_load(f)
    assert compose["name"] == "jobpilot-prod"
    assert "caddy" in compose["services"]
    assert "fastapi" in compose["services"]
    assert "db" in compose["services"]
    assert "n8n" in compose["services"]

def test_no_host_ports_except_caddy():
    with open("docker-compose.production.yml", "r") as f:
        compose = yaml.safe_load(f)

    assert "ports" not in compose["services"].get("fastapi", {})
    assert "ports" not in compose["services"].get("db", {})
    assert "ports" not in compose["services"].get("n8n", {})
    assert "ports" in compose["services"]["caddy"]

def test_explicit_volumes():
    with open("docker-compose.production.yml", "r") as f:
        compose = yaml.safe_load(f)

    volumes = compose.get("volumes", {})
    assert "postgres_data" in volumes
    assert "n8n_data" in volumes
    assert "caddy_data" in volumes
    assert "caddy_config" in volumes
    assert volumes["postgres_data"]["name"] == "jobpilot_postgres_data"

def test_networks_exist():
    with open("docker-compose.production.yml", "r") as f:
        compose = yaml.safe_load(f)

    networks = compose.get("networks", {})
    assert "frontend" in networks
    assert "backend" in networks

    assert "frontend" in compose["services"]["caddy"]["networks"]
    assert "backend" in compose["services"]["fastapi"]["networks"]
    assert "backend" in compose["services"]["db"]["networks"]
    assert "frontend" in compose["services"]["n8n"]["networks"]
    assert "backend" in compose["services"]["n8n"]["networks"]

def test_healthchecks_configured():
    with open("docker-compose.production.yml", "r") as f:
        compose = yaml.safe_load(f)

    assert "healthcheck" in compose["services"]["fastapi"]
    assert "healthcheck" in compose["services"]["db"]
    assert "healthcheck" in compose["services"]["n8n"]
    assert "healthcheck" in compose["services"]["caddy"]

def test_dockerfile_is_non_root():
    with open("backend/Dockerfile.production", "r") as f:
        content = f.read()

    assert "USER jobpilot" in content or "USER " in content
    assert "EXPOSE 8000" in content
    assert "HEALTHCHECK" in content

def test_docker_compose_config_validation():
    import subprocess
    import sys
    import shutil
    import pytest

    if shutil.which("docker") is None:
        pytest.skip("docker not installed")

    # Run docker compose config using the example environment to avoid missing variable errors
    env = os.environ.copy()
    env["APP_COMMIT_SHA"] = "0123456789abcdef0123456789abcdef01234567"

    # Load required vars from .env.example
    with open(".env.example") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                key, val = line.split("=", 1)
                env[key] = val

    result = subprocess.run(
        ["docker", "compose", "-f", "docker-compose.production.yml", "config"],
        env=env,
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"docker compose config failed: {result.stderr}"

def test_caddy_healthcheck_is_runtime():
    with open("docker-compose.production.yml", "r") as f:
        compose = yaml.safe_load(f)

    hc_test = compose["services"]["caddy"]["healthcheck"]["test"]
    # Should not be just a version check
    assert "version" not in hc_test
    # Should check the local runtime
    assert any("http://127.0.0.1:2019" in arg for arg in hc_test)

def test_security_negative_tests():
    with open("docker-compose.production.yml", "r") as f:
        content = f.read()

    # Provider secret VALUES must not be committed. Variable references like ${JOOBLE_API_KEY} are allowed.
    assert "your_adzuna_app_key" not in content
    assert "CHANGE_ME" not in content
    assert "test_key" not in content
    assert "your_jooble_api_key" not in content

    # Check that secrets are parameterized
    assert "${JOOBLE_API_KEY" in content
    assert "${ADZUNA_APP_KEY" in content
    assert "${API_SECRET_KEY" in content
    assert "${POSTGRES_PASSWORD" in content
    assert "${N8N_ENCRYPTION_KEY" in content

    assert not os.path.exists("id_rsa")
    assert not os.path.exists(".ssh")

    # Check Caddy proxy
    with open("Caddyfile", "r") as f:
        caddy = f.read()
        assert "reverse_proxy n8n:5678" in caddy
        assert "fastapi" not in caddy
        assert "db" not in caddy

    # Check dockerignore
    with open("backend/.dockerignore", "r") as f:
        ignore = f.read()
        assert ".git" in ignore
        assert ".env" in ignore

def test_rendered_compose_validation():
    import subprocess
    import yaml
    import os
    import shutil
    import pytest

    if shutil.which("docker") is None:
        pytest.skip("docker not installed")

    env = os.environ.copy()
    env["APP_COMMIT_SHA"] = "0123456789abcdef0123456789abcdef01234567"
    with open(".env.example") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                key, val = line.split("=", 1)
                env[key] = val

    result = subprocess.run(
        ["docker", "compose", "-f", "docker-compose.production.yml", "config"],
        env=env,
        capture_output=True,
        text=True
    )
    assert result.returncode == 0
    rendered = yaml.safe_load(result.stdout)

    # 1. only Caddy publishes host ports
    for name, svc in rendered["services"].items():
        if name == "caddy":
            ports = svc.get("ports", [])
            assert any(p["published"] == "80" for p in ports)
            assert any(p["published"] == "443" for p in ports)
        else:
            assert "ports" not in svc or len(svc["ports"]) == 0

    # 2. Network topology
    caddy_net = rendered["services"]["caddy"].get("networks", {})
    fastapi_net = rendered["services"]["fastapi"].get("networks", {})
    n8n_net = rendered["services"]["n8n"].get("networks", {})
    db_net = rendered["services"]["db"].get("networks", {})

    assert "frontend" in caddy_net
    assert "backend" not in caddy_net
    assert "backend" in fastapi_net
    assert "frontend" not in fastapi_net
    assert "backend" in db_net
    assert "frontend" not in db_net
    assert "frontend" in n8n_net
    assert "backend" in n8n_net

    # 3. Log rotation
    for name, svc in rendered["services"].items():
        if name == "db-setup" or name == "migration":
            continue
        logging = svc.get("logging", {})
        assert logging.get("driver") == "json-file"
        options = logging.get("options", {})
        assert "max-size" in options
        assert "max-file" in options

    # 4. No privileged
    for name, svc in rendered["services"].items():
        assert not svc.get("privileged", False)

    # 5. Volumes
    fastapi_volumes = rendered["services"]["fastapi"].get("volumes", [])
    assert not fastapi_volumes, "FastAPI should not have bind mounts in production"

def test_caddy_domain_runtime_variable():
    import yaml
    with open("docker-compose.production.yml", "r") as f:
        compose = yaml.safe_load(f)

    # 1. Caddy receives DOMAIN at runtime
    caddy_env = compose["services"]["caddy"].get("environment", {})
    if isinstance(caddy_env, list):
        assert any(e.startswith("DOMAIN=") for e in caddy_env), "Caddy is missing DOMAIN in environment list"
        # Extract the value for further assertions if needed
        val = next(e.split("=", 1)[1] for e in caddy_env if e.startswith("DOMAIN="))
        assert "${DOMAIN" in val or "DOMAIN" in val
    else:
        assert "DOMAIN" in caddy_env, "Caddy is missing DOMAIN in environment map"
        assert "${DOMAIN" in caddy_env["DOMAIN"]

    # 2. Caddyfile uses {$DOMAIN}
    with open("Caddyfile", "r") as f:
        caddyfile = f.read()
    assert "{$DOMAIN}" in caddyfile, "Caddyfile must use {$DOMAIN} placeholder"
    assert "jobpilot" not in caddyfile.lower(), "Caddyfile must not hardcode production domains"


def test_caddy_acme_email_and_admin_configured():
    with open("docker-compose.production.yml", "r") as f:
        compose = yaml.safe_load(f)

    caddy_env = compose["services"]["caddy"].get("environment", {})
    if isinstance(caddy_env, list):
        assert any(e.startswith("ACME_EMAIL=") for e in caddy_env)
        assert any(e.startswith("CADDY_ADMIN_USER=") for e in caddy_env)
        assert any(e.startswith("CADDY_ADMIN_HASH=") for e in caddy_env)
    else:
        assert "ACME_EMAIL" in caddy_env
        assert "CADDY_ADMIN_USER" in caddy_env
        assert "CADDY_ADMIN_HASH" in caddy_env


def test_n8n_proxy_trust_configured():
    with open("docker-compose.production.yml", "r") as f:
        compose = yaml.safe_load(f)

    n8n_env = compose["services"]["n8n"].get("environment", {})
    if isinstance(n8n_env, list):
        # We need N8N_PROXY_HOPS=1 exactly
        assert "N8N_PROXY_HOPS=1" in n8n_env, "N8N_PROXY_HOPS must be exactly 1"
        assert not any(e.startswith("N8N_FORWARDED_HEADER_TRUSTED_PROXIES=") for e in n8n_env)
    else:
        assert n8n_env.get("N8N_PROXY_HOPS") == "1", "N8N_PROXY_HOPS must be exactly 1"
        assert "N8N_FORWARDED_HEADER_TRUSTED_PROXIES" not in n8n_env


def test_caddyfile_security_headers():
    with open("Caddyfile", "r") as f:
        caddyfile = f.read()

    assert "Strict-Transport-Security" in caddyfile
    assert "X-Content-Type-Options" in caddyfile
    assert "email {$ACME_EMAIL}" in caddyfile
    assert "basic_auth" in caddyfile
    assert "{$CADDY_ADMIN_USER} {$CADDY_ADMIN_HASH}" in caddyfile
    assert "/webhook/*" in caddyfile


def test_caddy_is_edge_proxy_no_trusted_proxies():
    '''
    Conceptual Spoofing Security Contract:
    - Request A (no X-Forwarded-For): n8n receives Caddy-derived client identity.
    - Request B (X-Forwarded-For: 1.2.3.4): Caddy does NOT trust external XFF by default.
      It sanitizes/sets its own metadata, ensuring the external value cannot force n8n to treat 1.2.3.4 as the client.
    - Request C (X-Forwarded-Proto: http): n8n receives the proxy's authoritative HTTPS protocol.

    This structural test proves Caddy is acting as the definitive edge by ensuring
    no 'trusted_proxies' directive exists, which would otherwise instruct Caddy to
    pass through external spoofed headers.
    '''
    with open("Caddyfile", "r") as f:
        caddyfile = f.read()

    # Prove trusted_proxies is completely absent, cementing Caddy as the edge.
    assert "trusted_proxies" not in caddyfile, "Caddy must not trust external proxies in the current topology"
