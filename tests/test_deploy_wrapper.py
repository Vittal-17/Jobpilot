import pytest
import os
import sys
import tarfile
import hashlib
import tempfile
import io
import shutil
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../scripts/vps_setup")))
import deploy_wrapper

@pytest.fixture(autouse=True)
def restore_cwd():
    old_cwd = os.getcwd()
    yield
    os.chdir(old_cwd)

@pytest.fixture
def test_release_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d

@pytest.fixture
def mock_env(test_release_dir):
    with patch.dict(os.environ, {
        "SSH_ORIGINAL_COMMAND": f"{'a'*40} {'b'*64}"
    }, clear=True), patch("deploy_wrapper.BASE_RELEASE_DIR", test_release_dir):
        yield os.environ

@pytest.fixture
def mock_stdin():
    with patch("sys.stdin.buffer.read") as m_read:
        m_read.return_value = b""
        yield m_read

@pytest.fixture
def mock_execv():
    with patch("os.execv") as m_execv:
        yield m_execv

def test_invalid_commands(mock_env):
    invalid_cmds = [
        "abc", "a"*40, f"{'a'*40} {'b'*64} extra", f"{'a'*40}; {'b'*64}",
        f"{'a'*40} {'b'*64}&", f"`echo {'a'*40}` {'b'*64}", f"$(echo {'a'*40}) {'b'*64}"
    ]
    for cmd in invalid_cmds:
        mock_env["SSH_ORIGINAL_COMMAND"] = cmd
        with pytest.raises(SystemExit) as e:
            deploy_wrapper.main()
        assert e.value.code == 1

def test_empty_input(mock_env, mock_stdin):
    mock_stdin.return_value = b""
    with pytest.raises(SystemExit) as e:
        deploy_wrapper.main()
    assert e.value.code == 1

def test_oversized_input(mock_env, mock_stdin):
    mock_stdin.return_value = b"x" * (1048576 + 1)
    with pytest.raises(SystemExit) as e:
        deploy_wrapper.main()
    assert e.value.code == 1

def test_invalid_digest(mock_env, mock_stdin):
    mock_stdin.side_effect = [b"dummy data", b""]
    with pytest.raises(SystemExit) as e:
        deploy_wrapper.main()
    assert e.value.code == 1

def create_valid_tar(sha, digest="sha256:" + "c"*64):
    mem_file = io.BytesIO()
    with tarfile.open(fileobj=mem_file, mode="w") as tar:
        for name, mtype in [
            ("docker-compose.production.yml", tarfile.REGTYPE),
            ("scripts", tarfile.DIRTYPE),
            ("scripts/deploy.py", tarfile.REGTYPE),
            ("Caddyfile", tarfile.REGTYPE),
            ("release_sha.txt", tarfile.REGTYPE),
            ("image_digest.txt", tarfile.REGTYPE)
        ]:
            if mtype == tarfile.DIRTYPE:
                ti = tarfile.TarInfo(name)
                ti.type = mtype
                tar.addfile(ti)
            else:
                ti = tarfile.TarInfo(name)
                ti.type = mtype
                content = b"content"
                if name == "release_sha.txt":
                    content = sha.encode() + b"\n"
                elif name == "image_digest.txt":
                    content = digest.encode() + b"\n"
                ti.size = len(content)
                tar.addfile(ti, io.BytesIO(content))
    return mem_file.getvalue()

def test_valid_archive_execution(mock_env, mock_stdin, mock_execv, test_release_dir):
    sha = "a"*40
    tar_data = create_valid_tar(sha)
    tar_sha = hashlib.sha256(tar_data).hexdigest()

    mock_env["SSH_ORIGINAL_COMMAND"] = f"{sha} {tar_sha}"
    chunks = [tar_data[i:i+4096] for i in range(0, len(tar_data), 4096)] + [b""]
    mock_stdin.side_effect = chunks

    deploy_wrapper.main()

    mock_execv.assert_called_once()
    assert os.path.exists(os.path.join(test_release_dir, sha, "scripts/deploy.py"))

def test_cleanup_on_failure_but_preserve_existing(mock_env, mock_stdin, test_release_dir):
    sha = "a"*40
    tar_data = create_valid_tar(sha)
    tar_sha = hashlib.sha256(tar_data).hexdigest()
    mock_env["SSH_ORIGINAL_COMMAND"] = f"{sha} {tar_sha}"

    release_dir = os.path.join(test_release_dir, sha)
    os.makedirs(release_dir) # Pre-create it!

    with pytest.raises(SystemExit):
        deploy_wrapper.main()

    assert os.path.exists(release_dir)

def test_path_traversal(mock_env, mock_stdin, test_release_dir):
    sha = "a"*40
    mem_file = io.BytesIO()
    with tarfile.open(fileobj=mem_file, mode="w") as tar:
        ti = tarfile.TarInfo("../escaped.txt")
        ti.type = tarfile.REGTYPE
        ti.size = 2
        tar.addfile(ti, io.BytesIO(b"no"))

    tar_data = mem_file.getvalue()
    tar_sha = hashlib.sha256(tar_data).hexdigest()
    mock_env["SSH_ORIGINAL_COMMAND"] = f"{sha} {tar_sha}"
    mock_stdin.side_effect = [tar_data, b""]

    with pytest.raises(SystemExit):
        deploy_wrapper.main()

def test_sha_mismatch(mock_env, mock_stdin, test_release_dir):
    sha = "a"*40
    tar_data = create_valid_tar("b"*40)
    tar_sha = hashlib.sha256(tar_data).hexdigest()
    mock_env["SSH_ORIGINAL_COMMAND"] = f"{sha} {tar_sha}"
    mock_stdin.side_effect = [tar_data, b""]

    with pytest.raises(SystemExit):
        deploy_wrapper.main()

def test_invalid_image_digest_format(mock_env, mock_stdin, test_release_dir):
    sha = "a"*40
    tar_data = create_valid_tar(sha, digest="invalid-digest-format")
    tar_sha = hashlib.sha256(tar_data).hexdigest()
    mock_env["SSH_ORIGINAL_COMMAND"] = f"{sha} {tar_sha}"
    mock_stdin.side_effect = [tar_data, b""]

    with pytest.raises(SystemExit):
        deploy_wrapper.main()

def test_missing_archive_members(mock_env, mock_stdin, test_release_dir):
    sha = "a"*40
    mem_file = io.BytesIO()
    with tarfile.open(fileobj=mem_file, mode="w") as tar:
        for name, mtype in [
            ("docker-compose.production.yml", tarfile.REGTYPE),
            ("scripts", tarfile.DIRTYPE),
            ("scripts/deploy.py", tarfile.REGTYPE),
            ("release_sha.txt", tarfile.REGTYPE),
            ("image_digest.txt", tarfile.REGTYPE)
        ]:
            if mtype == tarfile.DIRTYPE:
                ti = tarfile.TarInfo(name)
                ti.type = mtype
                tar.addfile(ti)
            else:
                ti = tarfile.TarInfo(name)
                ti.type = mtype
                content = b"content"
                if name == "release_sha.txt":
                    content = sha.encode() + b"\n"
                elif name == "image_digest.txt":
                    content = b"sha256:" + b"c"*64 + b"\n"
                ti.size = len(content)
                tar.addfile(ti, io.BytesIO(content))
    tar_data = mem_file.getvalue()
    tar_sha = hashlib.sha256(tar_data).hexdigest()
    mock_env["SSH_ORIGINAL_COMMAND"] = f"{sha} {tar_sha}"
    mock_stdin.side_effect = [tar_data, b""]

    with pytest.raises(SystemExit):
        deploy_wrapper.main()

def test_unexpected_archive_members(mock_env, mock_stdin, test_release_dir):
    sha = "a"*40
    mem_file = io.BytesIO()
    with tarfile.open(fileobj=mem_file, mode="w") as tar:
        ti = tarfile.TarInfo("unexpected.txt")
        ti.type = tarfile.REGTYPE
        ti.size = 2
        tar.addfile(ti, io.BytesIO(b"no"))
    tar_data = mem_file.getvalue()
    tar_sha = hashlib.sha256(tar_data).hexdigest()
    mock_env["SSH_ORIGINAL_COMMAND"] = f"{sha} {tar_sha}"
    mock_stdin.side_effect = [tar_data, b""]

    with pytest.raises(SystemExit):
        deploy_wrapper.main()

def test_duplicate_archive_members(mock_env, mock_stdin, test_release_dir):
    sha = "a"*40
    mem_file = io.BytesIO()
    with tarfile.open(fileobj=mem_file, mode="w") as tar:
        for i in range(2):
            ti = tarfile.TarInfo("release_sha.txt")
            ti.type = tarfile.REGTYPE
            content = sha.encode() + b"\n"
            ti.size = len(content)
            tar.addfile(ti, io.BytesIO(content))
    tar_data = mem_file.getvalue()
    tar_sha = hashlib.sha256(tar_data).hexdigest()
    mock_env["SSH_ORIGINAL_COMMAND"] = f"{sha} {tar_sha}"
    mock_stdin.side_effect = [tar_data, b""]

    with pytest.raises(SystemExit):
        deploy_wrapper.main()

def test_non_bytes_stdin_does_not_loop(mock_env, test_release_dir):
    sha = "a"*40
    tar_sha = hashlib.sha256(b"dummy").hexdigest()
    mock_env["SSH_ORIGINAL_COMMAND"] = f"{sha} {tar_sha}"

    with patch("sys.stdin.buffer.read") as m_read:
        m_read.return_value = MagicMock()
        with pytest.raises(SystemExit) as e:
            deploy_wrapper.main()
        assert e.value.code == 1

def test_env_vars_ignored_for_release_dir_and_python(mock_env, mock_stdin, test_release_dir):
    # First, assert that these variables don't even exist in the source code anymore
    wrapper_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../scripts/vps_setup/deploy_wrapper.py"))
    with open(wrapper_path, "r") as f:
        source_code = f.read()
    assert "JOBPILOT_TEST_RELEASE_DIR" not in source_code
    assert "JOBPILOT_TEST_PYTHON" not in source_code

    with patch.dict(os.environ, {"JOBPILOT_TEST_RELEASE_DIR": "/tmp/malicious/dir", "JOBPILOT_TEST_PYTHON": "/tmp/malicious/python"}):
        import importlib
        import scripts.vps_setup.deploy_wrapper as raw_wrapper
        importlib.reload(raw_wrapper)

        assert raw_wrapper.BASE_RELEASE_DIR == "/opt/jobpilot/releases"

        sha = "a"*40
        tar_data = create_valid_tar(sha)
        tar_sha = hashlib.sha256(tar_data).hexdigest()

        os.environ["SSH_ORIGINAL_COMMAND"] = f"{sha} {tar_sha}"

        chunks = [tar_data[i:i+4096] for i in range(0, len(tar_data), 4096)] + [b""]
        mock_stdin.side_effect = chunks

        # We must mock all disk I/O to avoid PermissionError while evaluating the hardcoded path
        with patch("os.makedirs") as m_makedirs, \
             patch("os.mkdir"), \
             patch("os.path.exists", return_value=False), \
             patch("os.open", return_value=1), \
             patch("os.fdopen", MagicMock()), \
             patch("os.remove"), \
             patch("tarfile.open") as m_tar, \
             patch("os.chdir") as m_chdir, \
             patch("os.execv") as m_execv:

            # Setup tar mock to yield valid members matching the allowed list
            mock_tar_instance = MagicMock()
            m_tar.return_value.__enter__.return_value = mock_tar_instance

            allowed = {
                "docker-compose.production.yml": tarfile.REGTYPE,
                "scripts": tarfile.DIRTYPE,
                "scripts/deploy.py": tarfile.REGTYPE,
                "Caddyfile": tarfile.REGTYPE,
                "release_sha.txt": tarfile.REGTYPE,
                "image_digest.txt": tarfile.REGTYPE
            }

            members = []
            for name, mtype in allowed.items():
                m = tarfile.TarInfo(name)
                m.type = mtype
                if name == "release_sha.txt":
                    m.size = 41
                elif name == "image_digest.txt":
                    m.size = 72
                else:
                    m.size = 4
                members.append(m)

            mock_tar_instance.getmembers.return_value = members

            def mock_extractfile(name):
                m = MagicMock()
                if hasattr(name, 'name'):
                    name = name.name
                if name == "release_sha.txt":
                    m.read.side_effect = [(sha + "\n").encode('utf-8'), b""]
                elif name == "image_digest.txt":
                    m.read.side_effect = [(b"sha256:" + b"c"*64 + b"\n"), b""]
                else:
                    m.read.side_effect = [b"data", b""]
                return m

            mock_tar_instance.extractfile.side_effect = mock_extractfile
            mock_tar_instance.getmember = lambda name: next(m for m in members if m.name == name)

            raw_wrapper.main()

            # 1. Assert release directory is EXACTLY /opt/jobpilot/releases/sha
            expected_dir = f"/opt/jobpilot/releases/{sha}"
            m_makedirs.assert_any_call(expected_dir, mode=0o700, exist_ok=False)
            m_chdir.assert_called_with(expected_dir)

            # 2. Assert execv receives EXACTLY /usr/bin/python3 (ignoring the injected ENV)
            m_execv.assert_called_once()
            args = m_execv.call_args[0]
            assert args[0] == "/usr/bin/python3"
            assert args[1][0] == "python3"
            assert args[1][1] == f"{expected_dir}/scripts/deploy.py"
