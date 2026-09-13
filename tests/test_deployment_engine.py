import pytest
import subprocess
import os
import sys
import json
import shutil
import time
from unittest import mock

sys.path.insert(0, os.path.abspath("scripts"))
try:
    import deploy
except ImportError:
    pass

@pytest.fixture(autouse=True)
def clean_locks():
    deploy.release_lock()
    if os.path.exists(".deploy_pending.json"): os.remove(".deploy_pending.json")
    if os.path.exists(".current_release.json"): os.remove(".current_release.json")
    if os.path.exists("backups"): shutil.rmtree("backups")
    yield
    deploy.release_lock()
    if os.path.exists(".deploy_pending.json"): os.remove(".deploy_pending.json")
    if os.path.exists(".current_release.json"): os.remove(".current_release.json")
    if os.path.exists("backups"): shutil.rmtree("backups")

# Existing Tests Adapted
def test_valid_sha_accepted():
    valid = "a" * 40
    assert deploy.validate_sha(valid) == valid

def test_malformed_sha_rejected():
    with pytest.raises(SystemExit): deploy.validate_sha("short")
    with pytest.raises(SystemExit): deploy.validate_sha("g" * 40)
    with pytest.raises(SystemExit): deploy.validate_sha("A" * 40)

def test_missing_sha_rejected():
    with pytest.raises(SystemExit): deploy.validate_sha("")

def test_latest_rejected_as_release_identity():
    with pytest.raises(SystemExit): deploy.validate_sha("latest")

def test_exact_sha_image_reference_generated():
    sha = "a" * 40
    with mock.patch("subprocess.run") as m_run:
        m_run.return_value.returncode = 0
        m_run.return_value.stdout = json.dumps([{"Architecture": "arm64", "Config": {"Env": [f"APP_COMMIT_SHA={sha}"]}}])
        assert deploy.validate_artifact(sha) == f"ghcr.io/vittal-17/jobpilot-fastapi:{sha}"

def test_arm64_mismatch_fails_closed():
    sha = "a" * 40
    with mock.patch("subprocess.run") as m_run:
        m_run.return_value.returncode = 0
        m_run.return_value.stdout = json.dumps([{"Architecture": "amd64", "Config": {"Env": [f"APP_COMMIT_SHA={sha}"]}}])
        with pytest.raises(SystemExit):
            deploy.validate_artifact(sha)

def test_deployment_metadata_deterministic():
    sha = "a" * 40
    image = f"jobpilot-fastapi:{sha}"
    prev_sha = "b" * 40
    metadata = deploy.init_metadata(sha, image, prev_sha)
    assert metadata["release_sha"] == sha
    assert metadata["image_reference"] == image
    assert metadata["previous_release_sha"] == prev_sha
    assert "deployment_timestamp" in metadata
    assert metadata["phase"] == "prepared"
    assert os.path.exists(".deploy_pending.json")

def test_deployed_release_verification_fails_for_tampered_suffix():
    sha = "a" * 40
    container_id = "cid123"
    inspect_mock_data = [{"Config": {"Image": f"jobpilot-fastapi:{sha}-tampered", "Env": [f"APP_COMMIT_SHA={sha}"]}}]
    with mock.patch("subprocess.run") as m_run:
        m_run.return_value.returncode = 0
        m_run.return_value.stdout = json.dumps(inspect_mock_data)
        with pytest.raises(SystemExit):
            deploy.verify_release(sha, container_id, {})

# New Tests for Migration & Backup Safety
def test_deployment_lock_concurrency_and_persistence():
    import os, fcntl, pytest
    deploy.acquire_lock()

    # Process B attempt (simulated by opening another FD and trying to lock)
    fd_b = os.open(deploy.LOCK_FILE, os.O_RDWR)
    with pytest.raises((IOError, BlockingIOError)):
        fcntl.flock(fd_b, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.close(fd_b)

    deploy.release_lock()

    # Now B can acquire it (and the file should still exist)
    assert os.path.exists(deploy.LOCK_FILE)
    fd_c = os.open(deploy.LOCK_FILE, os.O_RDWR)
    fcntl.flock(fd_c, fcntl.LOCK_EX | fcntl.LOCK_NB)
    fcntl.flock(fd_c, fcntl.LOCK_UN)
    os.close(fd_c)

def test_stale_lock_handling():
    with open(deploy.LOCK_FILE, "w") as f:
        f.write("99999999") # Assuming this PID doesn't exist
    deploy.acquire_lock() # Should not raise
    assert os.path.exists(deploy.LOCK_FILE)

def test_backup_directory_creation_and_deterministic_naming():
    metadata = {}
    with mock.patch("subprocess.run") as m_run, mock.patch("os.environ", {"POSTGRES_PASSWORD":"p", "POSTGRES_USER":"u", "POSTGRES_DB":"d"}):
        m_run.return_value.returncode = 0
        with mock.patch("os.path.getsize", return_value=100), mock.patch("builtins.open", mock.mock_open(read_data=b"dummy")), mock.patch("os.fsync"), mock.patch("os.rename"):
            deploy.create_backup("a"*40, metadata, {"POSTGRES_PASSWORD":"p", "POSTGRES_USER":"u", "POSTGRES_DB":"d"})
    assert os.path.exists("backups")
    assert metadata["backup"]["format"] == "custom"
    assert "checksum" in metadata["backup"]

def test_empty_backup_rejection():
    metadata = {}
    with mock.patch("subprocess.run") as m_run, mock.patch("os.environ", {"POSTGRES_PASSWORD":"p", "POSTGRES_USER":"u", "POSTGRES_DB":"d"}):
        m_run.return_value.returncode = 0
        with mock.patch("os.path.getsize", return_value=0), mock.patch("builtins.open", mock.mock_open()):
            with mock.patch("os.remove"):
                with pytest.raises(SystemExit):
                    deploy.create_backup("a"*40, metadata, {"POSTGRES_PASSWORD":"p", "POSTGRES_USER":"u", "POSTGRES_DB":"d"})

def test_pg_dump_failure_rejection():
    metadata = {}
    with mock.patch("subprocess.run") as m_run, mock.patch("os.environ", {"POSTGRES_PASSWORD":"p", "POSTGRES_USER":"u", "POSTGRES_DB":"d"}):
        m_run.return_value.returncode = 1
        m_run.return_value.stderr = b"connection refused"
        with mock.patch("builtins.open", mock.mock_open()):
            with mock.patch("os.remove"):
                with pytest.raises(SystemExit):
                    deploy.create_backup("a"*40, metadata, {"POSTGRES_PASSWORD":"p", "POSTGRES_USER":"u", "POSTGRES_DB":"d"})

def test_archive_integrity_validation_failure():
    metadata = {}
    with mock.patch("subprocess.run") as m_run, mock.patch("os.environ", {"POSTGRES_PASSWORD":"p", "POSTGRES_USER":"u", "POSTGRES_DB":"d"}):
        # We need the first run (pg_dump) to succeed, and second run (pg_restore -l) to fail
        def side_effect(*args, **kwargs):
            m = mock.Mock()
            if "pg_restore" in str(args[0]):
                m.returncode = 1
                m.stderr = b"invalid archive"
            else:
                m.returncode = 0
            return m
        m_run.side_effect = side_effect
        with mock.patch("os.path.getsize", return_value=100), mock.patch("builtins.open", mock.mock_open(read_data=b"data")):
            with pytest.raises(SystemExit):
                deploy.create_backup("a"*40, metadata, {"POSTGRES_PASSWORD":"p", "POSTGRES_USER":"u", "POSTGRES_DB":"d"})

def test_restore_verification_success_isolated():
    metadata = {"backup": {"file": "dummy.dump"}}
    with mock.patch("subprocess.run") as m_run, mock.patch("builtins.open", mock.mock_open()), mock.patch("os.fsync"), mock.patch("os.rename"):
        m_run.return_value.returncode = 0
        m_run.return_value.stderr = b""
        deploy.verify_restore(metadata)
    assert metadata["backup"]["restore_verified"] == True

def test_restore_verification_fails_closed_on_non_zero_with_fatal():
    metadata = {"backup": {"file": "dummy.dump"}}
    with mock.patch("subprocess.run") as m_run, mock.patch("builtins.open", mock.mock_open()), mock.patch("os.fsync"), mock.patch("os.rename"):
        def side_effect(*args, **kwargs):
            m = mock.Mock()
            if "pg_restore" in str(args[0]):
                m.returncode = 1
                m.stderr = b"FATAL: error"
            else:
                m.returncode = 0
            return m
        m_run.side_effect = side_effect
        with pytest.raises(SystemExit):
            deploy.verify_restore(metadata)

def test_restore_verification_fails_closed_on_non_zero_without_fatal():
    metadata = {"backup": {"file": "dummy.dump"}}
    with mock.patch("subprocess.run") as m_run, mock.patch("builtins.open", mock.mock_open()), mock.patch("os.fsync"), mock.patch("os.rename"):
        def side_effect(*args, **kwargs):
            m = mock.Mock()
            if "pg_restore" in str(args[0]):
                m.returncode = 1
                m.stderr = b"warning only"
            else:
                m.returncode = 0
            return m
        m_run.side_effect = side_effect
        with pytest.raises(SystemExit):
            deploy.verify_restore(metadata)

def test_restore_verification_succeeds_on_zero_with_warnings():
    metadata = {"backup": {"file": "dummy.dump"}}
    with mock.patch("subprocess.run") as m_run, mock.patch("builtins.open", mock.mock_open()), mock.patch("os.fsync"), mock.patch("os.rename"):
        def side_effect(*args, **kwargs):
            m = mock.Mock()
            if "pg_restore" in str(args[0]):
                m.returncode = 0
                m.stderr = b"some non-fatal warnings"
            else:
                m.returncode = 0
            return m
        m_run.side_effect = side_effect
        deploy.verify_restore(metadata)
        assert metadata["backup"]["restore_verified"] == True

def test_migration_failure_stops_rollout():
    metadata = {}
    with mock.patch("subprocess.run") as m_run:
        def side_effect(*args, **kwargs):
            m = mock.Mock()
            if "heads" in str(args[0]):
                m.returncode = 0
                m.stdout = "abcdef123456 (head)"
            else:
                m.returncode = 1
                m.stderr = "migration error"
                m.stdout = ""
            return m
        m_run.side_effect = side_effect
        with pytest.raises(SystemExit):
            deploy.execute_migration(metadata, {"POSTGRES_PASSWORD":"p", "POSTGRES_USER":"u", "POSTGRES_DB":"d"})
    assert metadata["phase"] == "failed"

def test_alembic_multi_head_verification_succeeds():
    metadata = {}
    with mock.patch("subprocess.run") as m_run, mock.patch("os.environ", {"POSTGRES_PASSWORD":"p", "POSTGRES_USER":"u", "POSTGRES_DB":"d"}):
        def side_effect(*args, **kwargs):
            m = mock.Mock()
            m.returncode = 0
            if "heads" in str(args[0]):
                m.stdout = "abcdef123456 (head)\nfedcba654321 (head)"
            elif "current" in str(args[0]):
                m.stdout = "fedcba654321 (head)\nabcdef123456 (head)"
            else:
                m.stdout = ""
            return m
        m_run.side_effect = side_effect
        deploy.execute_migration(metadata, {"POSTGRES_PASSWORD":"p", "POSTGRES_USER":"u", "POSTGRES_DB":"d"})
    assert metadata["phase"] == "migration_succeeded"

def test_alembic_multi_head_verification_fails_on_extra_head():
    metadata = {}
    with mock.patch("subprocess.run") as m_run, mock.patch("os.environ", {"POSTGRES_PASSWORD":"p", "POSTGRES_USER":"u", "POSTGRES_DB":"d"}):
        def side_effect(*args, **kwargs):
            m = mock.Mock()
            m.returncode = 0
            if "heads" in str(args[0]):
                m.stdout = "abcdef123456 (head)"
            elif "current" in str(args[0]):
                m.stdout = "abcdef123456 (head)\nfedcba654321 (head)"
            else:
                m.stdout = ""
            return m
        m_run.side_effect = side_effect
        with pytest.raises(SystemExit):
            deploy.execute_migration(metadata, {"POSTGRES_PASSWORD":"p", "POSTGRES_USER":"u", "POSTGRES_DB":"d"})

def test_alembic_multi_head_verification_fails_on_missing_head():
    metadata = {}
    with mock.patch("subprocess.run") as m_run, mock.patch("os.environ", {"POSTGRES_PASSWORD":"p", "POSTGRES_USER":"u", "POSTGRES_DB":"d"}):
        def side_effect(*args, **kwargs):
            m = mock.Mock()
            m.returncode = 0
            if "heads" in str(args[0]):
                m.stdout = "abcdef123456 (head)\nfedcba654321 (head)"
            elif "current" in str(args[0]):
                m.stdout = "abcdef123456 (head)"
            else:
                m.stdout = ""
            return m
        m_run.side_effect = side_effect
        with pytest.raises(SystemExit):
            deploy.execute_migration(metadata, {"POSTGRES_PASSWORD":"p", "POSTGRES_USER":"u", "POSTGRES_DB":"d"})


def test_subprocess_argv_contains_no_passwords():
    metadata = {}
    with mock.patch("subprocess.run") as m_run, mock.patch("os.environ", {"POSTGRES_PASSWORD":"super_secret_password", "POSTGRES_USER":"u", "POSTGRES_DB":"d"}):
        def side_effect(*args, **kwargs):
            m = mock.Mock()
            m.returncode = 0
            if "heads" in str(args[0]):
                m.stdout = "abcdef123456 (head)"
            elif "current" in str(args[0]):
                m.stdout = "abcdef123456 (head)"
            else:
                m.stdout = ""
            return m
        m_run.side_effect = side_effect
        deploy.execute_migration(metadata, {"POSTGRES_PASSWORD":"p", "POSTGRES_USER":"u", "POSTGRES_DB":"d"})
        for call in m_run.call_args_list:
            cmd = " ".join(call[0][0])
            assert "super_secret_password" not in cmd
            if "env" in call[1]:
                # Secret should only exist inside the env overlay
                assert call[1]["env"].get("PGPASSWORD") == "super_secret_password" or "POSTGRES_PASSWORD" in call[1]["env"]


def test_failed_deployment_never_becomes_current():
    sha = "a" * 40
    metadata = deploy.init_metadata(sha, "img", "prev")
    with pytest.raises(SystemExit):
        deploy.fail("simulated failure", metadata)
    assert metadata["phase"] == "failed"
    with open(".deploy_pending.json") as f:
        data = json.load(f)
        assert data["phase"] == "failed"
    assert not os.path.exists(".current_release.json")

def test_backup_metadata_contains_no_secrets():
    sha = "a" * 40
    metadata = deploy.init_metadata(sha, "img", "prev")
    with mock.patch("subprocess.run") as m_run, mock.patch("os.environ", {"POSTGRES_PASSWORD":"super_secret_password", "POSTGRES_USER":"u", "POSTGRES_DB":"d"}):
        m_run.return_value.returncode = 0
        with mock.patch("os.path.getsize", return_value=100), mock.patch("builtins.open", mock.mock_open(read_data=b"data")), mock.patch("os.fsync"), mock.patch("os.rename"):
            deploy.create_backup(sha, metadata, {"POSTGRES_PASSWORD":"p", "POSTGRES_USER":"u", "POSTGRES_DB":"d"})
    # Since open was mocked, we can't read the file. The metadata object holds the values.
    assert "super_secret_password" not in json.dumps(metadata)

def test_no_automatic_alembic_downgrade_occurs():
    with mock.patch("subprocess.run") as m_run:
        def side_effect(*args, **kwargs):
            m = mock.Mock()
            m.returncode = 0
            if "heads" in str(args[0]) or "current" in str(args[0]):
                m.stdout = "abcdef123456 (head)"
            else:
                m.stdout = ""
            return m
        m_run.side_effect = side_effect
        with mock.patch("os.environ", {"POSTGRES_PASSWORD":"p", "POSTGRES_USER":"u", "POSTGRES_DB":"d"}):
            deploy.execute_migration({}, {"POSTGRES_PASSWORD":"p", "POSTGRES_USER":"u", "POSTGRES_DB":"d"})
    # Check that 'alembic downgrade' was never called
    for call in m_run.call_args_list:
        cmd = " ".join(call[0][0])
        assert "downgrade" not in cmd
        assert "upgrade head" in cmd or "current" in cmd or "psql" in cmd or "heads" in cmd

def test_migration_invocations_exclude_no_build():
    metadata = {}
    with mock.patch("subprocess.run") as m_run, mock.patch("os.environ", {"POSTGRES_PASSWORD":"p", "POSTGRES_USER":"u", "POSTGRES_DB":"d"}):
        def side_effect(*args, **kwargs):
            m = mock.Mock()
            m.returncode = 0
            if "heads" in str(args[0]) or "current" in str(args[0]):
                m.stdout = "abcdef123456 (head)"
            else:
                m.stdout = ""
            return m
        m_run.side_effect = side_effect
        deploy.execute_migration(metadata, {"POSTGRES_PASSWORD":"p", "POSTGRES_USER":"u", "POSTGRES_DB":"d"})

        # Check all alembic calls
        alembic_calls = 0
        for call in m_run.call_args_list:
            cmd = " ".join(call[0][0])
            if "alembic" in cmd:
                assert "--no-build" not in cmd
                alembic_calls += 1

        assert alembic_calls == 3 # heads, upgrade, current

def test_backup_fails_safely_on_collision():
    metadata = {}
    with mock.patch("os.open", side_effect=FileExistsError("File exists")):
        with pytest.raises(SystemExit):
            deploy.create_backup("a"*40, metadata, {"POSTGRES_PASSWORD":"p", "POSTGRES_USER":"u", "POSTGRES_DB":"d"})

import multiprocessing
import time

def child_acquire_lock(q):
    try:
        from scripts import deploy
        deploy.acquire_lock()
        q.put("ACQUIRED")
        time.sleep(2)
        deploy.release_lock()
    except Exception as e:
        q.put(str(e))
    except SystemExit:
        q.put("FAILED_TO_ACQUIRE")

def test_deployment_lock_multiprocessing():
    q = multiprocessing.Queue()
    p = multiprocessing.Process(target=child_acquire_lock, args=(q,))
    p.start()

    # Wait for child to acquire
    msg = q.get(timeout=5)
    assert msg == "ACQUIRED"

    # Parent tries to acquire and fails
    with pytest.raises(SystemExit):
        deploy.acquire_lock()

    p.join()

    # After child releases, parent can acquire
    deploy.acquire_lock()
    deploy.release_lock()

    # Persistent lock file remains present
    assert os.path.exists(deploy.LOCK_FILE)


def test_domain_validation():
    from scripts.deploy import is_valid_acme_domain

    # Valid
    assert is_valid_acme_domain("jobpilot.example.com") is True
    assert is_valid_acme_domain("n8n.jobpilot.example.com") is True

    # Invalid
    assert is_valid_acme_domain("192.168.1.10") is False
    assert is_valid_acme_domain("2001:db8::1") is False
    assert is_valid_acme_domain("https://jobpilot.example.com") is False
    assert is_valid_acme_domain("jobpilot.example.com/path") is False
    assert is_valid_acme_domain("jobpilot.example.com:443") is False
    assert is_valid_acme_domain("") is False
    assert is_valid_acme_domain("   ") is False
    assert is_valid_acme_domain("-jobpilot.example.com") is False
    assert is_valid_acme_domain("jobpilot.example.com-") is False
    assert is_valid_acme_domain("a" * 64 + ".com") is False  # Label too long
    assert is_valid_acme_domain("local") is False  # No TLD


def test_placeholder_credential_rejection():
    from scripts.deploy import validate_secrets
    import pytest

    # Mock valid environment configuration mapping
    config = {
        "API_SECRET_KEY": "valid",
        "POSTGRES_PASSWORD": "valid",
        "N8N_DB_PASSWORD": "valid",
        "N8N_ENCRYPTION_KEY": "valid",
        "CADDY_ADMIN_HASH": "valid",
        "CADDY_ADMIN_USER": "valid",
        "DOMAIN": "jobpilot.example.com",
        "ACME_EMAIL": "test@example.com",
        "POSTGRES_DB": "valid",
        "POSTGRES_USER": "valid",
        "N8N_DB_NAME": "valid",
        "N8N_DB_USER": "valid"
    }

    # Verify success
    validate_secrets(config)

    # Missing required
    c2 = dict(config)
    del c2["API_SECRET_KEY"]
    with pytest.raises(SystemExit):
        validate_secrets(c2)

    # Empty required
    c2 = dict(config)
    c2["API_SECRET_KEY"] = "   "
    with pytest.raises(SystemExit):
        validate_secrets(c2)

    # Placeholder required
    c2 = dict(config)
    c2["API_SECRET_KEY"] = "__REPLACE_WITH_SECRET__"
    with pytest.raises(SystemExit):
        validate_secrets(c2)

    # Placeholder optional
    c2 = dict(config)
    c2["ADZUNA_APP_KEY"] = "__REPLACE_WITH_KEY__"
    with pytest.raises(SystemExit):
        validate_secrets(c2)

def test_env_parser_and_permissions(tmp_path, monkeypatch):
    from scripts.deploy import load_production_env, resolve_effective_config
    import os
    import pytest

    env_file = tmp_path / ".env"

    # 1. Test .env parsing handles quotes, equals signs, comments
    env_file.write_text("""# A comment
API_SECRET_KEY=valid
POSTGRES_PASSWORD="valid_with_quotes"
N8N_DB_PASSWORD='valid_single'
N8N_ENCRYPTION_KEY=val=contains=equals

CADDY_ADMIN_HASH=test
""")

    env_file.chmod(0o600)

    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        data = load_production_env(".env")
        assert data["API_SECRET_KEY"] == "valid"
        assert data["POSTGRES_PASSWORD"] == "valid_with_quotes"
        assert data["N8N_DB_PASSWORD"] == "valid_single"
        assert data["N8N_ENCRYPTION_KEY"] == "val=contains=equals"
        assert data["CADDY_ADMIN_HASH"] == "test"

        # 2. Test precedence (process env overrides .env)
        process_env = {"API_SECRET_KEY": "overridden"}
        config = resolve_effective_config(data, process_env)
        assert config["API_SECRET_KEY"] == "overridden"
        assert config["CADDY_ADMIN_HASH"] == "test"

        # 3. Test duplicates fail closed
        env_file.write_text("API_SECRET_KEY=a\nAPI_SECRET_KEY=b")
        with pytest.raises(SystemExit):
            load_production_env(".env")

        # 4. Test malformed fails closed
        env_file.write_text("API_SECRET_KEY")
        with pytest.raises(SystemExit):
            load_production_env(".env")

        # 5. Test permissions fail closed (0644)
        env_file.write_text("API_SECRET_KEY=a")
        env_file.chmod(0o644)
        with pytest.raises(SystemExit):
            load_production_env(".env")

        # 6. Test stricter permissions pass (0400)
        env_file.chmod(0o400)
        assert load_production_env(".env")["API_SECRET_KEY"] == "a"

    finally:
        os.chdir(original_cwd)

def test_restore_verification_no_password_in_args():
    metadata = {"backup": {"file": "dummy.dump"}}
    with mock.patch("subprocess.run") as m_run, mock.patch("builtins.open", mock.mock_open()), mock.patch("time.sleep"), mock.patch("os.fsync"), mock.patch("os.rename"):
        def side_effect(args, **kwargs):
            # Assert PGPASSWORD=verify is NEVER in args
            for arg in args:
                assert "PGPASSWORD=verify" not in arg

            # Assert PGPASSWORD is passed in env
            if "env" in kwargs:
                assert kwargs["env"].get("PGPASSWORD") == "verify"

            m = mock.MagicMock()
            m.returncode = 0
            m.stderr = b""
            return m
        m_run.side_effect = side_effect
        deploy.verify_restore(metadata)

def test_effective_configuration_propagates_to_subprocesses():
    import scripts.deploy as deploy_module
    from unittest.mock import MagicMock, patch
    import os

    captured_envs = []
    def fake_subprocess_run(*args, **kwargs):
        # Capture the environment exactly as it would be inherited or explicitly passed
        env_passed = kwargs.get("env")
        if env_passed is None:
            captured_envs.append(dict(os.environ))
        else:
            captured_envs.append(dict(env_passed))
        return MagicMock(returncode=0, stdout="{}", stderr="")

    # We patch os.environ carefully, clearing it to avoid host environment noise
    test_env = {"API_SECRET_KEY": "process-env-secret", "EXPECTED_IMAGE_DIGEST": "digest123"}
    with patch.dict(os.environ, test_env, clear=True):
        with patch("scripts.deploy.load_production_env", return_value={"API_SECRET_KEY": "dot-env-secret", "DOMAIN": "example.com", "POSTGRES_USER": "test"}):
            with patch("scripts.deploy.validate_secrets"):
                with patch("scripts.deploy.validate_environment"):
                    with patch("scripts.deploy.get_current_release", return_value={}):
                        with patch("scripts.deploy.validate_artifact", return_value="test_image"):
                            with patch("scripts.deploy.check_db_ready"):
                                with patch("scripts.deploy.create_backup"):
                                    with patch("scripts.deploy.verify_restore"):
                                        with patch("scripts.deploy.execute_migration"):
                                            with patch("scripts.deploy.wait_for_health"):
                                                with patch("scripts.deploy.verify_release"):
                                                    with patch("os.rename"):
                                                        with patch("sys.argv", ["deploy.py", "--sha", "a"*40]):
                                                            with patch("subprocess.run", side_effect=fake_subprocess_run):
                                                                try:
                                                                    deploy_module.main()
                                                                except SystemExit:
                                                                    pass

    # Verify that at least one subprocess captured the environment correctly
    assert len(captured_envs) > 0, "subprocess.run was not called"

    # Check the last captured environment (e.g. from start_deployment)
    for env in captured_envs:
        # shared .env values reach the subprocess environment
        assert env.get("DOMAIN") == "example.com"

        # process environment values override .env values
        assert env.get("API_SECRET_KEY") == "process-env-secret"

        # EXPECTED_IMAGE_DIGEST remains preserved from process env
        assert env.get("EXPECTED_IMAGE_DIGEST") == "digest123"

        # APP_COMMIT_SHA remains the exact requested SHA
        assert env.get("APP_COMMIT_SHA") == "a"*40

def test_migration_service_requires_api_secret_key():
    import yaml
    import os

    compose_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docker-compose.production.yml")
    with open(compose_path, 'r') as f:
        compose_data = yaml.safe_load(f)

    migration_service = compose_data.get('services', {}).get('migration')
    assert migration_service is not None, "Migration service not found in docker-compose.production.yml"

    env_vars = migration_service.get('environment', [])

    # We must explicitly check that API_SECRET_KEY is present and strictly required.
    # The requirement is it must be exactly: API_SECRET_KEY=${API_SECRET_KEY:?API_SECRET_KEY must be set}
    expected_env = "API_SECRET_KEY=${API_SECRET_KEY:?API_SECRET_KEY must be set}"
    assert expected_env in env_vars, f"Expected '{expected_env}' in migration environment, but got {env_vars}"
