import pytest
import subprocess
import os
import sys
import json
import shutil
from unittest import mock

# Need to import deploy.py. We can run it as a script or import it.
# We will import it directly by adding scripts/ to sys.path
sys.path.insert(0, os.path.abspath("scripts"))
try:
    import deploy
except ImportError:
    pass

def test_valid_sha_accepted():
    valid = "a" * 40
    assert deploy.validate_sha(valid) == valid
    valid2 = "0123456789abcdef0123456789abcdef01234567"
    assert deploy.validate_sha(valid2) == valid2

def test_malformed_sha_rejected():
    with pytest.raises(SystemExit):
        deploy.validate_sha("short")
    with pytest.raises(SystemExit):
        deploy.validate_sha("g" * 40) # invalid hex
    with pytest.raises(SystemExit):
        deploy.validate_sha("A" * 40) # uppercase hex rejected or allowed? The regex expects lowercase.

def test_missing_sha_rejected():
    with pytest.raises(SystemExit):
        deploy.validate_sha("")
    with pytest.raises(SystemExit):
        deploy.validate_sha(None)

def test_latest_rejected_as_release_identity():
    with pytest.raises(SystemExit):
        deploy.validate_sha("latest")

def test_exact_sha_image_reference_generated():
    sha = "a" * 40
    with mock.patch("subprocess.run") as m_run:
        m_run.return_value.returncode = 0
        m_run.return_value.stdout = json.dumps([{"Architecture": "arm64"}])
        image_name = deploy.validate_artifact(sha)
        assert image_name == f"jobpilot-fastapi:{sha}"

def test_insufficient_disk_preflight_fails():
    with mock.patch("shutil.disk_usage") as m_disk:
        mock_usage = mock.Mock()
        mock_usage.free = 1000 # very low
        m_disk.return_value = mock_usage

        with mock.patch("shutil.which") as m_which, mock.patch("os.path.exists") as m_exists:
            m_which.return_value = "/usr/bin/docker"
            m_exists.return_value = True

            with pytest.raises(SystemExit):
                deploy.validate_environment()

def test_docker_unavailable_preflight_fails_cleanly():
    with mock.patch("shutil.which", return_value=None):
        with pytest.raises(SystemExit):
            deploy.validate_environment()

def test_compose_rendering_failure_fails_cleanly():
    with mock.patch("shutil.which", return_value="/usr/bin/docker"):
        with mock.patch("os.path.exists", return_value=True):
            with mock.patch("shutil.disk_usage") as m_disk:
                mock_usage = mock.Mock()
                mock_usage.free = 5 * 1024 * 1024 * 1024
                m_disk.return_value = mock_usage
                with mock.patch("subprocess.run") as m_run:
                    m_run.return_value.returncode = 1
                    m_run.return_value.stderr = "Error parsing compose"
                    with pytest.raises(SystemExit):
                        deploy.validate_environment()

def test_deployment_metadata_deterministic():
    sha = "a" * 40
    image = f"jobpilot-fastapi:{sha}"
    prev_sha = "b" * 40

    metadata = deploy.record_metadata(sha, image, prev_sha)
    assert metadata["release_sha"] == sha
    assert metadata["image_reference"] == image
    assert metadata["previous_release_sha"] == prev_sha
    assert "deployment_timestamp" in metadata
    assert os.path.exists(".deploy_pending.json")
    os.remove(".deploy_pending.json")

def test_deployed_release_verification_succeeds():
    sha = "a" * 40
    container_id = "cid123"

    inspect_mock_data = [{
        "Config": {
            "Image": f"jobpilot-fastapi:{sha}",
            "Env": [f"APP_COMMIT_SHA={sha}", "OTHER=1"]
        }
    }]
    with mock.patch("subprocess.run") as m_run:
        m_run.return_value.returncode = 0
        m_run.return_value.stdout = json.dumps(inspect_mock_data)

        deploy.verify_release(sha, container_id) # should not raise

def test_deployed_release_verification_fails_for_mismatched_sha():
    sha = "a" * 40
    container_id = "cid123"

    inspect_mock_data = [{
        "Config": {
            "Image": "jobpilot-fastapi:bbbb",
            "Env": [f"APP_COMMIT_SHA=bbbb"]
        }
    }]
    with mock.patch("subprocess.run") as m_run:
        m_run.return_value.returncode = 0
        m_run.return_value.stdout = json.dumps(inspect_mock_data)

        with pytest.raises(SystemExit):
            deploy.verify_release(sha, container_id)

def test_no_provider_secrets_appear_in_generated_deployment_artifacts():
    sha = "a" * 40
    image = f"jobpilot-fastapi:{sha}"
    deploy.record_metadata(sha, image, None)
    with open(".deploy_pending.json") as f:
        content = f.read()
    assert "JOOBLE_API_KEY" not in content
    assert "test_key" not in content
    assert "ADZUNA_APP_KEY" not in content
    os.remove(".deploy_pending.json")

def test_arm64_mismatch_fails_closed():
    sha = "a" * 40
    with mock.patch("subprocess.run") as m_run:
        m_run.return_value.returncode = 0
        m_run.return_value.stdout = json.dumps([{"Architecture": "amd64"}])
        with pytest.raises(SystemExit):
            deploy.validate_artifact(sha)

def test_deployed_release_verification_fails_for_tampered_suffix():
    sha = "a" * 40
    container_id = "cid123"
    inspect_mock_data = [{
        "Config": {
            "Image": f"jobpilot-fastapi:{sha}-tampered",
            "Env": [f"APP_COMMIT_SHA={sha}"]
        }
    }]
    with mock.patch("subprocess.run") as m_run:
        m_run.return_value.returncode = 0
        m_run.return_value.stdout = json.dumps(inspect_mock_data)
        with pytest.raises(SystemExit):
            deploy.verify_release(sha, container_id)

def test_deployed_release_verification_succeeds_with_registry():
    sha = "a" * 40
    container_id = "cid123"
    inspect_mock_data = [{
        "Config": {
            "Image": f"registry.example.com/jobpilot-fastapi:{sha}",
            "Env": [f"APP_COMMIT_SHA={sha}"]
        }
    }]
    with mock.patch("subprocess.run") as m_run:
        m_run.return_value.returncode = 0
        m_run.return_value.stdout = json.dumps(inspect_mock_data)
        deploy.verify_release(sha, container_id) # should not raise
