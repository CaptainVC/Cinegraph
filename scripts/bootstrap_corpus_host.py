"""Bootstrap or verify the isolated Dev private-corpus transfer host boundary."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Final

_REPOSITORY_IMPORT_ROOT = Path(__file__).resolve().parents[1]
if os.fspath(_REPOSITORY_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(_REPOSITORY_IMPORT_ROOT))

from scripts import bootstrap_dev_host  # noqa: E402
from scripts.bootstrap_dev_host import BootstrapError, ExpectedPath  # noqa: E402
from scripts.dev_host_contract import (  # noqa: E402
    DEPLOY_HOME,
    SAFE_PATH,
    authorized_key_entry,
    validate_fingerprint,
)
from scripts.private_corpus_host_contract import (  # noqa: E402
    CORPUS_AUTHORIZED_KEYS,
    CORPUS_DISPATCH_PATH,
    CORPUS_GID,
    CORPUS_GROUP,
    CORPUS_HELPER_PATH,
    CORPUS_HOME,
    CORPUS_PASSWORD_FIELD,
    CORPUS_SHELL,
    CORPUS_SUDOERS_CONTENT,
    CORPUS_SUDOERS_PATH,
    CORPUS_UID,
    CORPUS_USER,
    DEV_PRIVATE_CORPUS_ROOT,
    LEGACY_TRANSFER_ONLY_SUDOERS_CONTENT,
    MINIMUM_PYTHON_VERSION,
    OBJECTS_ROOT,
    PRIVATE_CORPUS_ROOT,
    PROCESS_HELPER_PATH,
    PROCESSING_RECEIPTS_ROOT,
    PROCESSING_ROOT,
    PROCESSOR_REQUIRED_COMMANDS,
    QUARANTINE_ROOT,
    RECEIVER_REQUIRED_COMMANDS,
    TRANSACTIONS_ROOT,
    corpus_authorized_key_entry,
)

# Keep the existing deployment bootstrap entirely untouched; this command shares
# only its already-tested root-controlled file installation primitives.
REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
SOURCE_DISPATCH: Final = REPOSITORY_ROOT / "deploy/remote/corpus-dispatch.sh"
SOURCE_HELPER: Final = REPOSITORY_ROOT / "deploy/remote/receive-private-corpus.sh"
SOURCE_PROCESS_HELPER: Final = REPOSITORY_ROOT / "deploy/remote/process-private-corpus.sh"
DEPLOY_AUTHORIZED_KEYS: Final = DEPLOY_HOME / ".ssh/authorized_keys"
FORBIDDEN_GROUP_NAMES: Final = frozenset(
    {"adm", "admin", "docker", "sudo", "wheel", "cinegraph-deploy"}
)

DIRECTORY_CONTRACT: Final = (
    ExpectedPath(Path("/etc"), "directory", 0, 0, 0o755),
    ExpectedPath(
        Path("/etc/sudoers.d"),
        "directory",
        0,
        0,
        0o750,
        accepted_modes=frozenset({0o755}),
    ),
    ExpectedPath(Path("/home"), "directory", 0, 0, 0o755),
    ExpectedPath(Path("/opt"), "directory", 0, 0, 0o755),
    ExpectedPath(Path("/usr"), "directory", 0, 0, 0o755),
    ExpectedPath(Path("/usr/local"), "directory", 0, 0, 0o755),
    ExpectedPath(Path("/usr/local/libexec"), "directory", 0, 0, 0o755),
    ExpectedPath(Path("/usr/local/sbin"), "directory", 0, 0, 0o755),
    ExpectedPath(Path("/opt/cinegraph"), "directory", 0, 0, 0o750),
    ExpectedPath(Path("/opt/cinegraph/shared"), "directory", 0, 0, 0o750),
    ExpectedPath(DEPLOY_HOME, "directory", 0, 0, 0o755),
    ExpectedPath(DEPLOY_HOME / ".ssh", "directory", 0, 0, 0o755),
    ExpectedPath(CORPUS_HOME, "directory", 0, 0, 0o755),
    ExpectedPath(CORPUS_HOME / ".ssh", "directory", 0, 0, 0o755),
    ExpectedPath(PRIVATE_CORPUS_ROOT, "directory", 0, 0, 0o700),
    ExpectedPath(DEV_PRIVATE_CORPUS_ROOT, "directory", 0, 0, 0o700),
    ExpectedPath(TRANSACTIONS_ROOT, "directory", 0, 0, 0o700),
    ExpectedPath(OBJECTS_ROOT, "directory", 0, 0, 0o700),
    ExpectedPath(QUARANTINE_ROOT, "directory", 0, 0, 0o700),
    ExpectedPath(PROCESSING_ROOT, "directory", 0, 0, 0o700),
    ExpectedPath(PROCESSING_RECEIPTS_ROOT, "directory", 0, 0, 0o700),
)
FILE_CONTRACT: Final = (
    ExpectedPath(CORPUS_DISPATCH_PATH, "file", 0, 0, 0o755),
    ExpectedPath(CORPUS_HELPER_PATH, "file", 0, 0, 0o755),
    ExpectedPath(PROCESS_HELPER_PATH, "file", 0, 0, 0o755),
    ExpectedPath(CORPUS_SUDOERS_PATH, "file", 0, 0, 0o440),
    ExpectedPath(CORPUS_AUTHORIZED_KEYS, "file", 0, 0, 0o644),
    ExpectedPath(DEPLOY_AUTHORIZED_KEYS, "file", 0, 0, 0o644),
)
REFRESH_ADDED_DIRECTORIES: Final = frozenset(
    {PROCESSING_ROOT, PROCESSING_RECEIPTS_ROOT}
)


def _validate_platform_and_tools() -> None:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise BootstrapError("corpus host must be Linux x86_64")
    if sys.version_info < MINIMUM_PYTHON_VERSION:
        raise BootstrapError("corpus host Python is below the supported version")
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise BootstrapError("corpus bootstrap/check must run as root")
    required_runtime_commands = RECEIVER_REQUIRED_COMMANDS + PROCESSOR_REQUIRED_COMMANDS
    if any(shutil.which(item, path=SAFE_PATH) is None for item in required_runtime_commands):
        raise BootstrapError("a required corpus host command is missing")
    for command in ("getent", "groupadd", "install", "ssh-keygen", "useradd", "visudo"):
        if shutil.which(command, path=SAFE_PATH) is None:
            raise BootstrapError("a required corpus bootstrap command is missing")
    bootstrap_dev_host._require_success(["docker", "compose", "version"])


def _public_key(path: Path) -> str:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise BootstrapError("public key input is not root-controlled")
    return bootstrap_dev_host.read_single_public_key(path)


def _fingerprint_line(public_key: str) -> str:
    from scripts.dev_host_contract import validate_public_key_line

    validated = validate_public_key_line(public_key)
    blob = base64.b64decode(validated.split()[1], validate=True)
    encoded = base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii").rstrip("=")
    return validate_fingerprint(f"SHA256:{encoded}")


def _installed_deploy_public_key() -> str:
    expected = next(item for item in FILE_CONTRACT if item.path == DEPLOY_AUTHORIZED_KEYS)
    bootstrap_dev_host._verify_path(expected)
    try:
        raw = DEPLOY_AUTHORIZED_KEYS.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise BootstrapError("deployment authorization could not be read") from error
    marker = " ssh-ed25519 "
    if raw.count(marker) != 1 or not raw.endswith("\n"):
        raise BootstrapError("deployment authorization is not canonical")
    public_key = "ssh-ed25519 " + raw.split(marker, 1)[1].rstrip("\n")
    if raw != authorized_key_entry(public_key):
        raise BootstrapError("deployment authorization differs from its fixed contract")
    return public_key


def _account_exists() -> bool:
    import pwd

    getpwnam = getattr(pwd, "getpwnam")
    try:
        getpwnam(CORPUS_USER)
    except KeyError:
        return False
    return True


def _create_account() -> None:
    import grp

    getgrnam = getattr(grp, "getgrnam")
    try:
        group = getgrnam(CORPUS_GROUP)
    except KeyError:
        bootstrap_dev_host._require_success(["groupadd", "--gid", str(CORPUS_GID), CORPUS_GROUP])
    else:
        if group.gr_gid != CORPUS_GID:
            raise BootstrapError("corpus group has an unexpected GID")
    bootstrap_dev_host._require_success(
        [
            "useradd",
            "--uid",
            str(CORPUS_UID),
            "--gid",
            str(CORPUS_GID),
            "--home-dir",
            str(CORPUS_HOME),
            "--no-create-home",
            "--shell",
            CORPUS_SHELL,
            "--password",
            CORPUS_PASSWORD_FIELD,
            CORPUS_USER,
        ]
    )


def _verify_account() -> None:
    import grp
    import pwd

    getpwnam = getattr(pwd, "getpwnam")
    getgrnam = getattr(grp, "getgrnam")
    try:
        account = getpwnam(CORPUS_USER)
        group = getgrnam(CORPUS_GROUP)
    except KeyError as error:
        raise BootstrapError("corpus account or group is missing") from error
    if (
        account.pw_uid != CORPUS_UID
        or account.pw_gid != CORPUS_GID
        or Path(account.pw_dir) != CORPUS_HOME
        or account.pw_shell != CORPUS_SHELL
        or group.gr_gid != CORPUS_GID
    ):
        raise BootstrapError("corpus account identity is invalid")
    groups = set(bootstrap_dev_host._require_success(["id", "-Gn", CORPUS_USER]).stdout.split())
    if groups != {CORPUS_GROUP} or groups & FORBIDDEN_GROUP_NAMES:
        raise BootstrapError("corpus account has unexpected groups")
    shadow = (
        bootstrap_dev_host._require_success(["getent", "shadow", CORPUS_USER])
        .stdout.strip()
        .split(":", 2)
    )
    if len(shadow) < 2 or shadow[1] != CORPUS_PASSWORD_FIELD:
        raise BootstrapError("corpus account password field is invalid")


def _read_source(path: Path) -> bytes:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise BootstrapError("corpus bootstrap source is not root-controlled")
    return path.read_bytes()


def _validate_sudoers_candidate(content: bytes) -> None:
    with tempfile.NamedTemporaryFile("wb", delete=False, dir=CORPUS_SUDOERS_PATH.parent) as stream:
        stream.write(content)
        candidate = Path(stream.name)
    try:
        bootstrap_dev_host._require_success(["visudo", "-cf", str(candidate)])
    finally:
        candidate.unlink(missing_ok=True)


def _managed_content(corpus_public_key: str) -> dict[Path, bytes]:
    return {
        CORPUS_DISPATCH_PATH: _read_source(SOURCE_DISPATCH),
        CORPUS_HELPER_PATH: _read_source(SOURCE_HELPER),
        PROCESS_HELPER_PATH: _read_source(SOURCE_PROCESS_HELPER),
        CORPUS_SUDOERS_PATH: CORPUS_SUDOERS_CONTENT.encode("utf-8"),
        CORPUS_AUTHORIZED_KEYS: corpus_authorized_key_entry(corpus_public_key).encode("utf-8"),
    }


def _preflight_refresh_host_files(
    corpus_public_key: str,
) -> tuple[dict[Path, bytes], dict[Path, bytes]]:
    managed = _managed_content(corpus_public_key)
    by_path = {item.path: item for item in FILE_CONTRACT}
    installed: dict[Path, bytes] = {}
    for path, content in managed.items():
        if path == PROCESS_HELPER_PATH and not path.exists() and not path.is_symlink():
            continue
        bootstrap_dev_host._verify_path(by_path[path])
        installed[path] = path.read_bytes()
        if path == CORPUS_AUTHORIZED_KEYS and installed[path] != content:
            raise BootstrapError("corpus authorization differs from the reviewed key")
        if path == CORPUS_SUDOERS_PATH and installed[path] not in {
            content,
            LEGACY_TRANSFER_ONLY_SUDOERS_CONTENT.encode("utf-8"),
        }:
            raise BootstrapError("corpus sudoers differs from a supported contract")
    _validate_sudoers_candidate(managed[CORPUS_SUDOERS_PATH])
    return managed, installed


def _ensure_host_files(corpus_public_key: str, *, apply: bool, refresh_corpus_code: bool) -> None:
    by_path = {item.path: item for item in FILE_CONTRACT}
    if refresh_corpus_code:
        managed, installed = _preflight_refresh_host_files(corpus_public_key)
        # Install the inert helper and its grant before publishing the dispatcher
        # that can reach it, so interruption remains fail-closed.
        for path in (
            CORPUS_HELPER_PATH,
            PROCESS_HELPER_PATH,
            CORPUS_SUDOERS_PATH,
            CORPUS_DISPATCH_PATH,
        ):
            if path not in installed:
                bootstrap_dev_host._ensure_exact_file(by_path[path], managed[path], apply=True)
            elif installed[path] != managed[path]:
                bootstrap_dev_host._replace_exact_file(by_path[path], managed[path])
    else:
        managed = _managed_content(corpus_public_key)
        if apply and not CORPUS_SUDOERS_PATH.exists() and not CORPUS_SUDOERS_PATH.is_symlink():
            _validate_sudoers_candidate(managed[CORPUS_SUDOERS_PATH])
        for path, content in managed.items():
            bootstrap_dev_host._ensure_exact_file(by_path[path], content, apply=apply)
    for path, content in managed.items():
        bootstrap_dev_host._ensure_exact_file(by_path[path], content, apply=False)
    bootstrap_dev_host._require_success(["visudo", "-cf", str(CORPUS_SUDOERS_PATH)])


def bootstrap(
    *,
    public_key_file: Path,
    expected_key_fingerprint: str,
    expected_deploy_key_fingerprint: str,
    check: bool,
    refresh_corpus_code: bool = False,
) -> dict[str, str]:
    if check and refresh_corpus_code:
        raise BootstrapError("refresh cannot be combined with check")
    _validate_platform_and_tools()
    checkout_sha = bootstrap_dev_host._verify_bootstrap_checkout()
    corpus_key = _public_key(public_key_file)
    corpus_fingerprint = _fingerprint_line(corpus_key)
    deploy_key = _installed_deploy_public_key()
    deploy_fingerprint = _fingerprint_line(deploy_key)
    if (
        validate_fingerprint(expected_key_fingerprint) != corpus_fingerprint
        or validate_fingerprint(expected_deploy_key_fingerprint) != deploy_fingerprint
        or corpus_fingerprint == deploy_fingerprint
        or corpus_key == deploy_key
    ):
        raise BootstrapError("corpus and deployment key identities are invalid")
    if not _account_exists():
        if check or refresh_corpus_code:
            raise BootstrapError("corpus account is missing")
        _create_account()
    _verify_account()
    if refresh_corpus_code:
        # Perform every source, authorization, and sudoers check before creating
        # the newly introduced processing directories.
        _preflight_refresh_host_files(corpus_key)
    for expected in DIRECTORY_CONTRACT:
        if (
            refresh_corpus_code
            and expected.path in REFRESH_ADDED_DIRECTORIES
            and not expected.path.exists()
            and not expected.path.is_symlink()
        ):
            bootstrap_dev_host._create_directory(expected)
        elif check or refresh_corpus_code or expected.path == Path("/opt/cinegraph/shared"):
            bootstrap_dev_host._verify_path(expected)
        else:
            bootstrap_dev_host._create_directory(expected)
    _ensure_host_files(
        corpus_key,
        apply=not check,
        refresh_corpus_code=refresh_corpus_code,
    )
    return {
        "bootstrap_sha": checkout_sha,
        "mode": "refresh-corpus-code" if refresh_corpus_code else "check" if check else "apply",
        "status": "corpus-transfer-ready",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-key-file", required=True, type=Path)
    parser.add_argument("--expected-key-fingerprint", required=True)
    parser.add_argument("--expected-deploy-key-fingerprint", required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--refresh-corpus-code", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        result = bootstrap(
            public_key_file=arguments.public_key_file,
            expected_key_fingerprint=arguments.expected_key_fingerprint,
            expected_deploy_key_fingerprint=arguments.expected_deploy_key_fingerprint,
            check=arguments.check,
            refresh_corpus_code=arguments.refresh_corpus_code,
        )
    except (BootstrapError, OSError, ValueError):
        print("Dev corpus host bootstrap failed", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
