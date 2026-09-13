#!/usr/bin/env python3
"""
Deployment wrapper script for securely extracting and verifying a release payload.
Executes the actual deployment script if verification passes.
"""
import sys
import os
import re
import hashlib
import tarfile
import shutil
from typing import NoReturn

MAX_ARCHIVE_BYTES: int = 1048576 # 1MB
MAX_FILE_SIZE: int = 524288 # 500KB
MAX_FILES: int = 50

BASE_RELEASE_DIR: str = "/opt/jobpilot/releases"

def fail(msg: str) -> NoReturn:
    print(f"FATAL: {msg}", file=sys.stderr)
    sys.exit(1)

def main() -> None:
    cmd = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    if not re.compile(r"^[a-f0-9]{40} [a-f0-9]{64}$", re.ASCII).fullmatch(cmd):
        fail("Invalid SSH_ORIGINAL_COMMAND grammar")

    req_sha, req_tar_sha256 = cmd.split(" ")

    data = b""
    while len(data) <= MAX_ARCHIVE_BYTES:
        chunk = sys.stdin.buffer.read(4096)
        if not chunk:
            break
        if not isinstance(chunk, bytes):
            fail("Input stream returned non-bytes")
        data += chunk

    if len(data) > MAX_ARCHIVE_BYTES:
        fail("Archive size exceeds maximum allowed bound")

    if not data:
        fail("Empty input stream")

    local_tar_sha256 = hashlib.sha256(data).hexdigest()
    if local_tar_sha256 != req_tar_sha256:
        fail("Archive digest mismatch")

    release_dir = os.path.join(BASE_RELEASE_DIR, req_sha)
    tmp_tar = f"{release_dir}.tmp.tar"

    if os.path.exists(release_dir):
        fail("Release directory already exists")

    directory_created = False

    try:
        fd = os.open(tmp_tar, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)

        with tarfile.open(tmp_tar, "r") as tar:
            members = tar.getmembers()
            if len(members) > MAX_FILES:
                fail("Too many members in archive")

            allowed_files = {
                "docker-compose.production.yml": tarfile.REGTYPE,
                "scripts": tarfile.DIRTYPE,
                "scripts/deploy.py": tarfile.REGTYPE,
                "Caddyfile": tarfile.REGTYPE,
                "release_sha.txt": tarfile.REGTYPE,
                "image_digest.txt": tarfile.REGTYPE
            }

            seen = set()
            for m in members:
                norm_name = os.path.normpath(m.name)
                if norm_name not in allowed_files:
                    fail(f"Unexpected archive member: {norm_name}")
                if m.type != allowed_files[norm_name]:
                    fail(f"Unexpected archive member type for {norm_name}")
                if norm_name in seen:
                    fail(f"Duplicate archive member: {norm_name}")
                if m.size > MAX_FILE_SIZE:
                    fail(f"File size exceeds maximum allowed bound: {norm_name}")
                seen.add(norm_name)

            if seen != set(allowed_files.keys()):
                fail("Missing required archive members")

            sha_file = tar.extractfile("release_sha.txt")
            if not sha_file:
                fail("Could not read release_sha.txt")
            extracted_sha_bytes = sha_file.read()
            extracted_sha = extracted_sha_bytes.decode('utf-8').strip('\n')
            if extracted_sha != req_sha or len(extracted_sha) != 40:
                fail("release_sha.txt does not match requested SHA exactly")

            digest_file = tar.extractfile("image_digest.txt")
            if not digest_file:
                fail("Could not read image_digest.txt")
            extracted_digest_bytes = digest_file.read()
            extracted_digest = extracted_digest_bytes.decode('utf-8').strip('\n')
            if not re.compile(r"^sha256:[a-f0-9]{64}$", re.ASCII).fullmatch(extracted_digest):
                fail("Invalid image_digest.txt format")

            os.makedirs(release_dir, mode=0o700, exist_ok=False)
            directory_created = True

            for name, mtype in allowed_files.items():
                m = tar.getmember(name)
                target_path = os.path.abspath(os.path.join(release_dir, name))
                if not target_path.startswith(os.path.abspath(release_dir) + os.sep) and target_path != os.path.abspath(release_dir):
                    fail(f"Path traversal detected during extraction: {name}")

                if m.isdir():
                    if not os.path.exists(target_path):
                        os.mkdir(target_path, 0o700)
                elif m.isreg():
                    parent = os.path.dirname(target_path)
                    if not os.path.exists(parent):
                        os.makedirs(parent, mode=0o700)

                    f_obj = tar.extractfile(m)
                    if not f_obj:
                        fail(f"Failed to extract {name}")

                    fd_out = os.open(target_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd_out, "wb") as out:
                        copied = 0
                        while True:
                            buf = f_obj.read(4096)
                            if not buf:
                                break
                            copied += len(buf)
                            if copied > MAX_FILE_SIZE:
                                fail(f"Extracted file size exceeded for {name}")
                            out.write(buf)
                        if copied != m.size:
                            fail(f"Size mismatch during extraction of {name}")

    except SystemExit:
        if directory_created and os.path.exists(release_dir):
            shutil.rmtree(release_dir, ignore_errors=True)
        raise
    except Exception as e:
        if directory_created and os.path.exists(release_dir):
            shutil.rmtree(release_dir, ignore_errors=True)
        fail(f"Extraction failed: {e}")
    finally:
        if os.path.exists(tmp_tar):
            try:
                os.remove(tmp_tar)
            except OSError:
                pass

    deploy_script = os.path.join(release_dir, "scripts/deploy.py")

    os.chdir(release_dir)

    sys.stdout.flush()
    sys.stderr.flush()

    os.execv("/usr/bin/python3", ["python3", str(deploy_script), "--sha", req_sha])

if __name__ == "__main__":
    main()
