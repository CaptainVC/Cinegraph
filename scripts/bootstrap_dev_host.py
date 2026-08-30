"""Bootstrap or verify the locked-down Cinegraph Dev deployment host contract.

This command is intentionally run by an operator in the Hostinger console. It never
accepts a private key and never prints the Dev environment file.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from scripts.dev_host_contract import (
    CONFIG_ROOT,
    DEPLOY_GID,
    DEPLOY_GROUP,
    DEPLOY_HOME,
    DEPLOY_PASSWORD_FIELD,
    DEPLOY_ROOT,
    DEPLOY_SHELL,
    DEPLOY_UID,
    DEPLOY_USER,
    DEV_ENV_FILE,
    DISPATCH_PATH,
    HELPER_PATH,
    RELEASES_ROOT,
    REPOSITORY_URL,
    REQUIRED_COMMANDS,
    SAFE_PATH,
    SHARED_ROOT,
    SUDOERS_CONTENT,
    SUDOERS_PATH,
    authorized_key_entry,
    known_hosts_line,
    validate_fingerprint,
    validate_host,
    validate_public_key_line,
)

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
SOURCE_DISPATCH: Final = REPOSITORY_ROOT / "deploy/remote/deploy-dispatch.sh"
SOURCE_HELPER: Final = REPOSITORY_ROOT / "deploy/remote/deploy-dev.sh"
SOURCE_ENV: Final = REPOSITORY_ROOT / "deploy/env/dev.env.example"
HOST_PUBLIC_KEY: Final = Path("/etc/ssh/ssh_host_ed25519_key.pub")
AUTHORIZED_KEYS: Final = DEPLOY_HOME / ".ssh/authorized_keys"
FORBIDDEN_GROUPS: Final = frozenset({"adm", "admin", "docker", "sudo", "wheel"})


class BootstrapError(RuntimeError):
    """A fail-closed host contract violation."""


@dataclass(frozen=True, slots=True)
class ExpectedPath:
    path: Path
    kind: str
    uid: int
    gid: int
    mode: int


DIRECTORY_CONTRACT: Final = (
    ExpectedPath(Path("/etc"), "directory", 0, 0, 0o755),
    ExpectedPath(Path("/home"), "directory", 0, 0, 0o755),
    ExpectedPath(Path("/opt"), "directory", 0, 0, 0o755),
    ExpectedPath(Path("/usr"), "directory", 0, 0, 0o755),
    ExpectedPath(Path("/usr/bin"), "directory", 0, 0, 0o755),
    ExpectedPath(Path("/usr/sbin"), "directory", 0, 0, 0o755),
    ExpectedPath(Path("/usr/local"), "directory", 0, 0, 0o755),
    ExpectedPath(Path("/usr/local/libexec"), "directory", 0, 0, 0o755),
    ExpectedPath(Path("/usr/local/sbin"), "directory", 0, 0, 0o755),
    ExpectedPath(Path("/etc/sudoers.d"), "directory", 0, 0, 0o755),
    ExpectedPath(Path("/run"), "directory", 0, 0, 0o755),
    ExpectedPath(DEPLOY_ROOT, "directory", 0, 0, 0o750),
    ExpectedPath(RELEASES_ROOT, "directory", 0, 0, 0o750),
    ExpectedPath(SHARED_ROOT, "directory", 0, 0, 0o750),
    ExpectedPath(CONFIG_ROOT, "directory", 0, 0, 0o700),
    ExpectedPath(DEPLOY_HOME, "directory", 0, 0, 0o755),
    ExpectedPath(DEPLOY_HOME / ".ssh", "directory", 0, 0, 0o755),
)
FILE_CONTRACT: Final = (
    ExpectedPath(DISPATCH_PATH, "file", 0, 0, 0o755),
    ExpectedPath(HELPER_PATH, "file", 0, 0, 0o755),
    ExpectedPath(SUDOERS_PATH, "file", 0, 0, 0o440),
    ExpectedPath(AUTHORIZED_KEYS, "file", 0, 0, 0o644),
    ExpectedPath(DEV_ENV_FILE, "file", 0, 0, 0o600),
)


def _run(
    command: list[str],
    *,
    input_text: str | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        env={
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "PATH": SAFE_PATH,
        },
    )


def _require_success(
    command: list[str],
    *,
    input_text: str | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    result = _run(command, input_text=input_text, cwd=cwd)
    if result.returncode:
        raise BootstrapError(f"required command failed: {command[0]}")
    return result


def read_single_public_key(path: Path) -> str:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise BootstrapError("public key file is unavailable") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise BootstrapError("public key path must be a regular non-symlink file")
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise BootstrapError("public key file could not be read") from error
    lines = raw.splitlines()
    if len(lines) != 1 or not lines[0]:
        raise BootstrapError("public key file must contain exactly one nonempty line")
    try:
        return validate_public_key_line(lines[0])
    except ValueError as error:
        raise BootstrapError(str(error)) from error


def parse_ssh_keygen_fingerprint(output: str) -> str:
    fields = output.strip().split()
    if len(fields) < 2:
        raise BootstrapError("ssh-keygen did not return a fingerprint")
    try:
        return validate_fingerprint(fields[1])
    except ValueError as error:
        raise BootstrapError(str(error)) from error


def fingerprint(path: Path) -> str:
    result = _require_success(["ssh-keygen", "-lf", str(path), "-E", "sha256"])
    return parse_ssh_keygen_fingerprint(result.stdout)


def _validate_platform_and_tools() -> None:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise BootstrapError("Dev host must be Linux x86_64")
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise BootstrapError("bootstrap/check must run as root in the provider console")
    missing = [command for command in REQUIRED_COMMANDS if shutil.which(command, path=SAFE_PATH) is None]
    if missing:
        raise BootstrapError("required host commands are missing")
    _require_success(["docker", "info", "--format", "{{.Architecture}}"])
    _require_success(["docker", "compose", "version"])


def _verify_root_controlled_checkout_tree() -> None:
    roots = (REPOSITORY_ROOT, REPOSITORY_ROOT / ".git")
    for root in roots:
        metadata = _path_metadata(root)
        if not stat.S_ISDIR(metadata.st_mode):
            raise BootstrapError("bootstrap checkout must contain a regular .git directory")
    for path in (REPOSITORY_ROOT, *REPOSITORY_ROOT.rglob("*")):
        metadata = _path_metadata(path)
        if metadata.st_uid != 0 or metadata.st_gid != 0:
            raise BootstrapError("bootstrap checkout must be owned entirely by root")
        if stat.S_IMODE(metadata.st_mode) & 0o022:
            raise BootstrapError("bootstrap checkout must not be group/other writable")


def _verify_bootstrap_checkout() -> str:
    _verify_root_controlled_checkout_tree()
    remotes = _require_success(["git", "-C", str(REPOSITORY_ROOT), "remote"]).stdout.split()
    if remotes != ["origin"]:
        raise BootstrapError("bootstrap checkout must have only the approved origin remote")
    origin = _require_success(
        ["git", "-C", str(REPOSITORY_ROOT), "config", "--local", "--get", "remote.origin.url"]
    ).stdout.strip()
    if origin != REPOSITORY_URL:
        raise BootstrapError("bootstrap checkout origin is not approved")
    status = _require_success(
        ["git", "-C", str(REPOSITORY_ROOT), "status", "--porcelain=v1", "--untracked-files=all"]
    ).stdout
    if status:
        raise BootstrapError("bootstrap checkout must be clean, including untracked files")
    head = _require_success(
        ["git", "-C", str(REPOSITORY_ROOT), "rev-parse", "--verify", "HEAD"]
    ).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise BootstrapError("bootstrap checkout HEAD is not a canonical commit SHA")
    remote_main = _require_success(
        ["git", "ls-remote", "--exit-code", REPOSITORY_URL, "refs/heads/main"]
    ).stdout.strip()
    if remote_main != f"{head}\trefs/heads/main":
        raise BootstrapError("bootstrap checkout must exactly match the live main tip")
    return head


def _account_exists() -> bool:
    import pwd

    getpwnam = getattr(pwd, "getpwnam")
    try:
        getpwnam(DEPLOY_USER)
    except KeyError:
        return False
    return True


def _create_account() -> None:
    import grp

    getgrnam = getattr(grp, "getgrnam")
    try:
        existing_group = getgrnam(DEPLOY_GROUP)
    except KeyError:
        _require_success(["groupadd", "--gid", str(DEPLOY_GID), DEPLOY_GROUP])
    else:
        if existing_group.gr_gid != DEPLOY_GID:
            raise BootstrapError("deployment group has an unexpected GID")
    _require_success(
        [
            "useradd",
            "--uid",
            str(DEPLOY_UID),
            "--gid",
            str(DEPLOY_GID),
            "--home-dir",
            str(DEPLOY_HOME),
            "--no-create-home",
            "--shell",
            DEPLOY_SHELL,
            "--password",
            DEPLOY_PASSWORD_FIELD,
            DEPLOY_USER,
        ]
    )


def _verify_account() -> None:
    import grp
    import pwd

    getpwnam = getattr(pwd, "getpwnam")
    getgrnam = getattr(grp, "getgrnam")
    try:
        account = getpwnam(DEPLOY_USER)
        group = getgrnam(DEPLOY_GROUP)
    except KeyError as error:
        raise BootstrapError("deployment account or group is missing") from error
    if (
        account.pw_uid != DEPLOY_UID
        or account.pw_gid != DEPLOY_GID
        or Path(account.pw_dir) != DEPLOY_HOME
        or account.pw_shell != DEPLOY_SHELL
        or group.gr_gid != DEPLOY_GID
    ):
        raise BootstrapError("deployment account identity does not match the fixed contract")
    groups = set(_require_success(["id", "-Gn", DEPLOY_USER]).stdout.split())
    if groups != {DEPLOY_GROUP} or groups & FORBIDDEN_GROUPS:
        raise BootstrapError("deployment account has unexpected privileged or supplementary groups")
    shadow = _require_success(["getent", "shadow", DEPLOY_USER]).stdout.strip().split(":", 2)
    if len(shadow) < 2 or shadow[1] != DEPLOY_PASSWORD_FIELD:
        raise BootstrapError("deployment account password field is not disabled safely")


def _path_metadata(path: Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise BootstrapError(f"required path is missing: {path}") from error
    if stat.S_ISLNK(metadata.st_mode):
        raise BootstrapError(f"required path must not be a symlink: {path}")
    return metadata


def _verify_path(expected: ExpectedPath) -> None:
    metadata = _path_metadata(expected.path)
    kind_ok = stat.S_ISDIR(metadata.st_mode) if expected.kind == "directory" else stat.S_ISREG(metadata.st_mode)
    if not kind_ok:
        raise BootstrapError(f"required path has an unexpected type: {expected.path}")
    if metadata.st_uid != expected.uid or metadata.st_gid != expected.gid:
        raise BootstrapError(f"required path has unexpected ownership: {expected.path}")
    if stat.S_IMODE(metadata.st_mode) != expected.mode:
        raise BootstrapError(f"required path has unexpected mode: {expected.path}")


def _create_directory(expected: ExpectedPath) -> None:
    if expected.path.exists() or expected.path.is_symlink():
        _verify_path(expected)
        return
    _require_success(
        [
            "install",
            "-d",
            "-o",
            str(expected.uid),
            "-g",
            str(expected.gid),
            "-m",
            f"{expected.mode:o}",
            str(expected.path),
        ]
    )
    _verify_path(expected)


def _install_absent(path: Path, content: bytes, *, uid: int, gid: int, mode: int) -> None:
    if path.exists() or path.is_symlink():
        raise BootstrapError(f"refusing to overwrite existing path: {path}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        fchmod = getattr(os, "fchmod")
        fchown = getattr(os, "fchown")
        fchmod(descriptor, mode)
        fchown(descriptor, uid, gid)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary_path, path)
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise BootstrapError(f"managed file could not be installed safely: {path}") from error
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    finally:
        temporary_path.unlink(missing_ok=True)


def _ensure_exact_file(expected: ExpectedPath, content: bytes, *, apply: bool) -> None:
    if not expected.path.exists() and not expected.path.is_symlink():
        if not apply:
            raise BootstrapError(f"required file is missing: {expected.path}")
        _install_absent(expected.path, content, uid=expected.uid, gid=expected.gid, mode=expected.mode)
    _verify_path(expected)
    try:
        installed = expected.path.read_bytes()
    except OSError as error:
        raise BootstrapError(f"required file could not be read: {expected.path}") from error
    if installed != content:
        raise BootstrapError(f"existing managed file differs from the reviewed contract: {expected.path}")


def _read_source(path: Path) -> bytes:
    metadata = _path_metadata(path)
    if not stat.S_ISREG(metadata.st_mode):
        raise BootstrapError("managed source is not a regular file")
    try:
        return path.read_bytes()
    except OSError as error:
        raise BootstrapError("managed source could not be read") from error


def _ensure_host_files(public_key: str, *, apply: bool) -> None:
    managed = {
        DISPATCH_PATH: _read_source(SOURCE_DISPATCH),
        HELPER_PATH: _read_source(SOURCE_HELPER),
        SUDOERS_PATH: SUDOERS_CONTENT.encode("utf-8"),
        AUTHORIZED_KEYS: authorized_key_entry(public_key).encode("utf-8"),
    }
    by_path = {expected.path: expected for expected in FILE_CONTRACT}
    for path, content in managed.items():
        if path == SUDOERS_PATH and apply and not path.exists() and not path.is_symlink():
            with tempfile.NamedTemporaryFile("wb", delete=False, dir=SUDOERS_PATH.parent) as stream:
                stream.write(content)
                sudoers_candidate = Path(stream.name)
            try:
                os.chmod(sudoers_candidate, 0o440)
                _require_success(["visudo", "-cf", str(sudoers_candidate)])
            finally:
                sudoers_candidate.unlink(missing_ok=True)
        _ensure_exact_file(by_path[path], content, apply=apply)
    if not DEV_ENV_FILE.exists() and not DEV_ENV_FILE.is_symlink():
        if not apply:
            raise BootstrapError("Dev environment file is missing")
        _install_absent(DEV_ENV_FILE, _read_source(SOURCE_ENV), uid=0, gid=0, mode=0o600)
    _verify_path(by_path[DEV_ENV_FILE])
    _require_success(["visudo", "-cf", str(SUDOERS_PATH)])


def _host_evidence(host: str, mode: str) -> dict[str, str]:
    host_key = read_single_public_key(HOST_PUBLIC_KEY)
    host_fingerprint = fingerprint(HOST_PUBLIC_KEY)
    docker_arch = _require_success(["docker", "info", "--format", "{{.Architecture}}"]).stdout.strip()
    if docker_arch not in {"amd64", "x86_64"}:
        raise BootstrapError("Docker server architecture is not amd64")
    return {
        "deploy_user": DEPLOY_USER,
        "docker_arch": "amd64",
        "host": host,
        "host_arch": "x86_64",
        "host_key_fingerprint": host_fingerprint,
        "known_hosts": known_hosts_line(host, host_key),
        "mode": mode,
        "status": "activation-ready" if mode == "check" else "bootstrap-applied",
    }


def _verify_runtime_contract() -> None:
    _require_success(
        [
            "python3",
            "-B",
            "-m",
            "scripts.validate_vps_runtime",
            "--environment",
            "development",
            "--env-file",
            str(DEV_ENV_FILE),
            "--compose-file",
            str(REPOSITORY_ROOT / "deploy/compose.yaml"),
        ],
        cwd=REPOSITORY_ROOT,
    )


def bootstrap(*, public_key_file: Path, expected_fingerprint: str, host: str, check: bool) -> dict[str, str]:
    _validate_platform_and_tools()
    checkout_sha = _verify_bootstrap_checkout()
    try:
        validate_host(host)
        validate_fingerprint(expected_fingerprint)
    except ValueError as error:
        raise BootstrapError(str(error)) from error
    public_key = read_single_public_key(public_key_file)
    if fingerprint(public_key_file) != expected_fingerprint:
        raise BootstrapError("deployment public-key fingerprint does not match the operator value")
    if not _account_exists():
        if check:
            raise BootstrapError("deployment account is missing")
        _create_account()
    _verify_account()
    for expected in DIRECTORY_CONTRACT:
        if expected.path in {
            Path("/etc"),
            Path("/home"),
            Path("/opt"),
            Path("/usr"),
            Path("/usr/bin"),
            Path("/usr/sbin"),
            Path("/usr/local"),
            Path("/run"),
        }:
            _verify_path(expected)
        elif check:
            _verify_path(expected)
        else:
            _create_directory(expected)
    _ensure_host_files(public_key, apply=not check)
    if check:
        _verify_runtime_contract()
    evidence = _host_evidence(host, "check" if check else "apply")
    evidence["bootstrap_sha"] = checkout_sha
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-key-file", type=Path, required=True)
    parser.add_argument("--expected-key-fingerprint", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        evidence = bootstrap(
            public_key_file=arguments.public_key_file,
            expected_fingerprint=arguments.expected_key_fingerprint,
            host=arguments.host,
            check=arguments.check,
        )
    except BootstrapError as error:
        print(f"Dev host bootstrap failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
