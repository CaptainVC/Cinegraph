"""Bootstrap or verify the dedicated paid speaker-review SSH identity."""

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

_ROOT = Path(__file__).resolve().parents[1]
if os.fspath(_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(_ROOT))

from scripts import bootstrap_corpus_host, bootstrap_dev_host  # noqa: E402
from scripts.bootstrap_dev_host import BootstrapError, ExpectedPath  # noqa: E402
from scripts.dev_host_contract import DEPLOY_HOME, SAFE_PATH, validate_fingerprint  # noqa: E402
from scripts.private_speaker_review_first_adjudication_host_contract import (  # noqa: E402
    REVIEW_FIRST_ADJUDICATION_HELPER_PATH,
    REVIEW_FIRST_ADJUDICATION_RECEIPTS_ROOT,
)
from scripts.private_speaker_review_first_adjudication_host_contract import (  # noqa: E402
    SUDOERS_CONTENT as FIRST_ADJUDICATION_SUDOERS_CONTENT,
)
from scripts.private_speaker_review_first_adjudication_observation_host_contract import (  # noqa: E402
    REVIEW_FIRST_ADJUDICATION_OBSERVATION_HELPER_PATH,
    REVIEW_FIRST_ADJUDICATION_OBSERVATION_RECEIPTS_ROOT,
)
from scripts.private_speaker_review_first_adjudication_observation_host_contract import (  # noqa: E402
    SUDOERS_CONTENT as FIRST_ADJUDICATION_OBSERVATION_SUDOERS_CONTENT,
)
from scripts.private_speaker_review_next_primary_host_contract import (  # noqa: E402
    REVIEW_NEXT_PRIMARY_HELPER_PATH,
    REVIEW_NEXT_PRIMARY_RECEIPTS_ROOT,
)
from scripts.private_speaker_review_next_primary_host_contract import (  # noqa: E402
    SUDOERS_CONTENT as NEXT_PRIMARY_SUDOERS_CONTENT,
)
from scripts.private_speaker_review_next_primary_observation_host_contract import (  # noqa: E402
    REVIEW_NEXT_OBSERVATION_HELPER_PATH,
)
from scripts.private_speaker_review_next_primary_observation_host_contract import (  # noqa: E402
    SUDOERS_CONTENT as NEXT_OBSERVATION_SUDOERS_CONTENT,
)
from scripts.private_speaker_review_observation_host_contract import (  # noqa: E402
    REVIEW_OBSERVATION_RECEIPTS_ROOT,
)
from scripts.private_speaker_review_primary_result_processing_host_contract import (  # noqa: E402
    REVIEW_PRIMARY_RESULT_PROCESSING_HELPER_PATH,
    REVIEW_PRIMARY_RESULT_PROCESSING_RECEIPTS_ROOT,
)
from scripts.private_speaker_review_primary_result_processing_host_contract import (  # noqa: E402
    SUDOERS_CONTENT as PRIMARY_RESULT_PROCESSING_SUDOERS_CONTENT,
)
from scripts.private_speaker_review_submission_host_contract import (  # noqa: E402
    BOOTSTRAP_COMMANDS,
    DEPLOY_ROOT,
    LEGACY_SUDOERS_CONTENT,
    MINIMUM_PYTHON_VERSION,
    RELEASES_ROOT,
    REVIEW_AUTHORIZATION_ROOT,
    REVIEW_AUTHORIZED_KEYS,
    REVIEW_DISPATCH_PATH,
    REVIEW_GID,
    REVIEW_GROUP,
    REVIEW_HELPER_PATH,
    REVIEW_HOME,
    REVIEW_OBSERVATION_HELPER_PATH,
    REVIEW_PASSWORD_FIELD,
    REVIEW_SHELL,
    REVIEW_SUBMISSION_RECEIPTS_ROOT,
    REVIEW_SUDOERS_PATH,
    REVIEW_UID,
    REVIEW_USER,
    SHARED_ROOT,
    SPEAKER_REVIEW_ROOT,
    SPEAKER_REVIEW_RUNS_ROOT,
    authorized_key_entry,
)
from scripts.private_speaker_review_submission_host_contract import (  # noqa: E402
    SUDOERS_CONTENT as OBSERVATION_SUDOERS_CONTENT,
)

REPOSITORY_ROOT: Final = _ROOT
SOURCE_DISPATCH: Final = REPOSITORY_ROOT / "deploy/remote/review-dispatch.sh"
SOURCE_HELPER: Final = REPOSITORY_ROOT / "deploy/remote/submit-private-speaker-review.sh"
SOURCE_OBSERVATION_HELPER: Final = (
    REPOSITORY_ROOT / "deploy/remote/observe-private-speaker-review.sh"
)
SOURCE_NEXT_PRIMARY_HELPER: Final = (
    REPOSITORY_ROOT / "deploy/remote/submit-next-private-speaker-review.sh"
)
SOURCE_NEXT_OBSERVATION_HELPER: Final = (
    REPOSITORY_ROOT / "deploy/remote/observe-next-private-speaker-review.sh"
)
SOURCE_PRIMARY_RESULT_PROCESSING_HELPER: Final = (
    REPOSITORY_ROOT / "deploy/remote/process-private-speaker-review-results.sh"
)
SOURCE_FIRST_ADJUDICATION_HELPER: Final = (
    REPOSITORY_ROOT / "deploy/remote/submit-first-private-speaker-review-adjudication.sh"
)
SOURCE_FIRST_ADJUDICATION_OBSERVATION_HELPER: Final = (
    REPOSITORY_ROOT / "deploy/remote/observe-first-private-speaker-review-adjudication.sh"
)
FORBIDDEN_GROUP_NAMES: Final = frozenset(
    {"adm", "admin", "docker", "sudo", "wheel", "cinegraph-deploy", "cinegraph-corpus"}
)

DIRECTORY_CONTRACT: Final = (
    ExpectedPath(Path("/etc"), "directory", 0, 0, 0o755),
    ExpectedPath(Path("/etc/sudoers.d"), "directory", 0, 0, 0o750, frozenset({0o755})),
    ExpectedPath(Path("/home"), "directory", 0, 0, 0o755),
    ExpectedPath(Path("/opt"), "directory", 0, 0, 0o755),
    ExpectedPath(Path("/usr"), "directory", 0, 0, 0o755),
    ExpectedPath(Path("/usr/local"), "directory", 0, 0, 0o755),
    ExpectedPath(Path("/usr/local/libexec"), "directory", 0, 0, 0o755),
    ExpectedPath(Path("/usr/local/sbin"), "directory", 0, 0, 0o755),
    ExpectedPath(DEPLOY_ROOT, "directory", 0, 0, 0o750),
    ExpectedPath(RELEASES_ROOT, "directory", 0, 0, 0o750),
    ExpectedPath(SHARED_ROOT, "directory", 0, 0, 0o750),
    ExpectedPath(REVIEW_HOME, "directory", 0, 0, 0o755),
    ExpectedPath(REVIEW_HOME / ".ssh", "directory", 0, 0, 0o755),
    ExpectedPath(SPEAKER_REVIEW_ROOT, "directory", 0, 0, 0o700),
    ExpectedPath(REVIEW_AUTHORIZATION_ROOT, "directory", 0, 0, 0o700),
    ExpectedPath(REVIEW_SUBMISSION_RECEIPTS_ROOT, "directory", 0, 0, 0o700),
    ExpectedPath(REVIEW_OBSERVATION_RECEIPTS_ROOT, "directory", 0, 0, 0o700),
    ExpectedPath(REVIEW_NEXT_PRIMARY_RECEIPTS_ROOT, "directory", 0, 0, 0o700),
    ExpectedPath(REVIEW_PRIMARY_RESULT_PROCESSING_RECEIPTS_ROOT, "directory", 0, 0, 0o700),
    ExpectedPath(REVIEW_FIRST_ADJUDICATION_RECEIPTS_ROOT, "directory", 0, 0, 0o700),
    ExpectedPath(
        REVIEW_FIRST_ADJUDICATION_OBSERVATION_RECEIPTS_ROOT,
        "directory",
        0,
        0,
        0o700,
    ),
    ExpectedPath(SPEAKER_REVIEW_RUNS_ROOT, "directory", 0, 0, 0o700),
)
FILE_CONTRACT: Final = (
    ExpectedPath(REVIEW_DISPATCH_PATH, "file", 0, 0, 0o755),
    ExpectedPath(REVIEW_HELPER_PATH, "file", 0, 0, 0o755),
    ExpectedPath(REVIEW_OBSERVATION_HELPER_PATH, "file", 0, 0, 0o755),
    ExpectedPath(REVIEW_NEXT_PRIMARY_HELPER_PATH, "file", 0, 0, 0o755),
    ExpectedPath(REVIEW_NEXT_OBSERVATION_HELPER_PATH, "file", 0, 0, 0o755),
    ExpectedPath(REVIEW_PRIMARY_RESULT_PROCESSING_HELPER_PATH, "file", 0, 0, 0o755),
    ExpectedPath(REVIEW_FIRST_ADJUDICATION_HELPER_PATH, "file", 0, 0, 0o755),
    ExpectedPath(
        REVIEW_FIRST_ADJUDICATION_OBSERVATION_HELPER_PATH,
        "file",
        0,
        0,
        0o755,
    ),
    ExpectedPath(REVIEW_SUDOERS_PATH, "file", 0, 0, 0o440),
    ExpectedPath(REVIEW_AUTHORIZED_KEYS, "file", 0, 0, 0o644),
)


def _validate_platform_and_tools() -> None:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise BootstrapError("review host must be Linux x86_64")
    if sys.version_info < MINIMUM_PYTHON_VERSION:
        raise BootstrapError("review host Python is below the supported version")
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise BootstrapError("review bootstrap/check must run as root")
    if any(shutil.which(command, path=SAFE_PATH) is None for command in BOOTSTRAP_COMMANDS):
        raise BootstrapError("a required review bootstrap command is missing")
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


def _account_exists() -> bool:
    import pwd

    try:
        pwd.getpwnam(REVIEW_USER)
    except KeyError:
        return False
    return True


def _create_account() -> None:
    import grp

    try:
        group = grp.getgrnam(REVIEW_GROUP)
    except KeyError:
        bootstrap_dev_host._require_success(["groupadd", "--gid", str(REVIEW_GID), REVIEW_GROUP])
    else:
        if group.gr_gid != REVIEW_GID:
            raise BootstrapError("review group has an unexpected GID")
    bootstrap_dev_host._require_success(
        [
            "useradd",
            "--uid",
            str(REVIEW_UID),
            "--gid",
            str(REVIEW_GID),
            "--home-dir",
            str(REVIEW_HOME),
            "--no-create-home",
            "--shell",
            REVIEW_SHELL,
            "--password",
            REVIEW_PASSWORD_FIELD,
            REVIEW_USER,
        ]
    )


def _verify_account() -> None:
    import grp
    import pwd

    try:
        account = pwd.getpwnam(REVIEW_USER)
        group = grp.getgrnam(REVIEW_GROUP)
    except KeyError as error:
        raise BootstrapError("review account or group is missing") from error
    if (
        account.pw_uid != REVIEW_UID
        or account.pw_gid != REVIEW_GID
        or Path(account.pw_dir) != REVIEW_HOME
        or account.pw_shell != REVIEW_SHELL
        or group.gr_gid != REVIEW_GID
    ):
        raise BootstrapError("review account identity is invalid")
    groups = set(bootstrap_dev_host._require_success(["id", "-Gn", REVIEW_USER]).stdout.split())
    if groups != {REVIEW_GROUP} or groups & FORBIDDEN_GROUP_NAMES:
        raise BootstrapError("review account has unexpected groups")
    shadow = (
        bootstrap_dev_host._require_success(["getent", "shadow", REVIEW_USER])
        .stdout.strip()
        .split(":", 2)
    )
    if len(shadow) < 2 or shadow[1] != REVIEW_PASSWORD_FIELD:
        raise BootstrapError("review account password field is invalid")


def _read_source(path: Path) -> bytes:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise BootstrapError("review bootstrap source is not root-controlled")
    return path.read_bytes()


def _validate_sudoers_candidate(content: bytes) -> None:
    with tempfile.NamedTemporaryFile("wb", delete=False, dir=REVIEW_SUDOERS_PATH.parent) as stream:
        stream.write(content)
        candidate = Path(stream.name)
    try:
        bootstrap_dev_host._require_success(["visudo", "-cf", str(candidate)])
    finally:
        candidate.unlink(missing_ok=True)


def _managed_content(public_key: str) -> dict[Path, bytes]:
    return {
        REVIEW_HELPER_PATH: _read_source(SOURCE_HELPER),
        REVIEW_OBSERVATION_HELPER_PATH: _read_source(SOURCE_OBSERVATION_HELPER),
        REVIEW_NEXT_PRIMARY_HELPER_PATH: _read_source(SOURCE_NEXT_PRIMARY_HELPER),
        REVIEW_NEXT_OBSERVATION_HELPER_PATH: _read_source(SOURCE_NEXT_OBSERVATION_HELPER),
        REVIEW_PRIMARY_RESULT_PROCESSING_HELPER_PATH: _read_source(
            SOURCE_PRIMARY_RESULT_PROCESSING_HELPER
        ),
        REVIEW_FIRST_ADJUDICATION_HELPER_PATH: _read_source(SOURCE_FIRST_ADJUDICATION_HELPER),
        REVIEW_FIRST_ADJUDICATION_OBSERVATION_HELPER_PATH: _read_source(
            SOURCE_FIRST_ADJUDICATION_OBSERVATION_HELPER
        ),
        REVIEW_SUDOERS_PATH: FIRST_ADJUDICATION_OBSERVATION_SUDOERS_CONTENT.encode("utf-8"),
        REVIEW_AUTHORIZED_KEYS: authorized_key_entry(public_key).encode("utf-8"),
        REVIEW_DISPATCH_PATH: _read_source(SOURCE_DISPATCH),
    }


def _preflight_refresh_host_files(
    public_key: str,
) -> tuple[dict[Path, bytes], dict[Path, bytes]]:
    """Validate the installed identity boundary before replacing code files."""

    managed = _managed_content(public_key)
    by_path = {item.path: item for item in FILE_CONTRACT}
    installed: dict[Path, bytes] = {}
    for path, content in managed.items():
        expected = by_path[path]
        if (
            path
            in {
                REVIEW_OBSERVATION_HELPER_PATH,
                REVIEW_NEXT_PRIMARY_HELPER_PATH,
                REVIEW_NEXT_OBSERVATION_HELPER_PATH,
                REVIEW_PRIMARY_RESULT_PROCESSING_HELPER_PATH,
                REVIEW_FIRST_ADJUDICATION_HELPER_PATH,
                REVIEW_FIRST_ADJUDICATION_OBSERVATION_HELPER_PATH,
            }
            and not path.exists()
            and not path.is_symlink()
        ):
            continue
        bootstrap_dev_host._verify_path(expected)
        try:
            installed[path] = path.read_bytes()
        except OSError as error:
            raise BootstrapError(f"required file could not be read: {path}") from error
        if path == REVIEW_AUTHORIZED_KEYS and installed[path] != content:
            raise BootstrapError("review authorization differs from the reviewed key")
        if path == REVIEW_SUDOERS_PATH and installed[path] not in {
            content,
            NEXT_OBSERVATION_SUDOERS_CONTENT.encode("utf-8"),
            NEXT_PRIMARY_SUDOERS_CONTENT.encode("utf-8"),
            OBSERVATION_SUDOERS_CONTENT.encode("utf-8"),
            LEGACY_SUDOERS_CONTENT.encode("utf-8"),
            PRIMARY_RESULT_PROCESSING_SUDOERS_CONTENT.encode("utf-8"),
            FIRST_ADJUDICATION_SUDOERS_CONTENT.encode("utf-8"),
            FIRST_ADJUDICATION_OBSERVATION_SUDOERS_CONTENT.encode("utf-8"),
        }:
            raise BootstrapError("review sudoers differs from the reviewed contract")
    _validate_sudoers_candidate(managed[REVIEW_SUDOERS_PATH])
    bootstrap_dev_host._require_success(["visudo", "-cf", str(REVIEW_SUDOERS_PATH)])
    return managed, installed


def _installed_deploy_public_key() -> str:
    expected = next(
        item
        for item in bootstrap_corpus_host.FILE_CONTRACT
        if item.path == DEPLOY_HOME / ".ssh/authorized_keys"
    )
    bootstrap_dev_host._verify_path(expected)
    raw = expected.path.read_text(encoding="utf-8")
    marker = " ssh-ed25519 "
    if raw.count(marker) != 1 or not raw.endswith("\n"):
        raise BootstrapError("deployment authorization is not canonical")
    return "ssh-ed25519 " + raw.split(marker, 1)[1].rstrip("\n")


def _verify_existing_corpus_boundary(
    *,
    corpus_public_key_file: Path,
    expected_corpus_key_fingerprint: str,
    expected_deploy_key_fingerprint: str,
) -> None:
    bootstrap_corpus_host.bootstrap(
        public_key_file=corpus_public_key_file,
        expected_key_fingerprint=expected_corpus_key_fingerprint,
        expected_deploy_key_fingerprint=expected_deploy_key_fingerprint,
        check=True,
    )


def _ensure_host_files(public_key: str, *, apply: bool, refresh_review_code: bool = False) -> None:
    by_path = {item.path: item for item in FILE_CONTRACT}
    if refresh_review_code:
        managed, installed = _preflight_refresh_host_files(public_key)
        # Keep the account authorization unchanged. Install every reviewed
        # helper before expanding the exact sudo policy, then replace the
        # dispatcher last so a new command is never reachable early.
        for path in (
            REVIEW_HELPER_PATH,
            REVIEW_OBSERVATION_HELPER_PATH,
            REVIEW_NEXT_PRIMARY_HELPER_PATH,
            REVIEW_NEXT_OBSERVATION_HELPER_PATH,
            REVIEW_PRIMARY_RESULT_PROCESSING_HELPER_PATH,
            REVIEW_FIRST_ADJUDICATION_HELPER_PATH,
            REVIEW_FIRST_ADJUDICATION_OBSERVATION_HELPER_PATH,
        ):
            if path not in managed:
                continue
            if path not in installed:
                bootstrap_dev_host._ensure_exact_file(by_path[path], managed[path], apply=True)
            elif installed[path] != managed[path]:
                bootstrap_dev_host._replace_exact_file(by_path[path], managed[path])
        for path in (
            REVIEW_SUDOERS_PATH,
            REVIEW_DISPATCH_PATH,
        ):
            if path not in installed or installed[path] != managed[path]:
                bootstrap_dev_host._replace_exact_file(by_path[path], managed[path])
        for path, content in managed.items():
            bootstrap_dev_host._ensure_exact_file(by_path[path], content, apply=False)
        bootstrap_dev_host._require_success(["visudo", "-cf", str(REVIEW_SUDOERS_PATH)])
        return
    managed = _managed_content(public_key)
    if apply and not REVIEW_SUDOERS_PATH.exists() and not REVIEW_SUDOERS_PATH.is_symlink():
        _validate_sudoers_candidate(managed[REVIEW_SUDOERS_PATH])
    for path, content in managed.items():
        bootstrap_dev_host._ensure_exact_file(by_path[path], content, apply=apply)
    for path, content in managed.items():
        bootstrap_dev_host._ensure_exact_file(by_path[path], content, apply=False)
    bootstrap_dev_host._require_success(["visudo", "-cf", str(REVIEW_SUDOERS_PATH)])


def bootstrap(
    *,
    public_key_file: Path,
    expected_key_fingerprint: str,
    corpus_public_key_file: Path,
    expected_corpus_key_fingerprint: str,
    expected_deploy_key_fingerprint: str,
    check: bool,
    refresh_review_code: bool = False,
) -> dict[str, str]:
    if check and refresh_review_code:
        raise BootstrapError("refresh cannot be combined with check")
    _validate_platform_and_tools()
    checkout_sha = bootstrap_dev_host._verify_bootstrap_checkout()
    review_key = _public_key(public_key_file)
    corpus_key = bootstrap_corpus_host._public_key(corpus_public_key_file)
    deploy_key = _installed_deploy_public_key()
    review_fingerprint = _fingerprint_line(review_key)
    corpus_fingerprint = _fingerprint_line(corpus_key)
    deploy_fingerprint = _fingerprint_line(deploy_key)
    if (
        validate_fingerprint(expected_key_fingerprint) != review_fingerprint
        or validate_fingerprint(expected_corpus_key_fingerprint) != corpus_fingerprint
        or validate_fingerprint(expected_deploy_key_fingerprint) != deploy_fingerprint
        or len({review_key, corpus_key, deploy_key}) != 3
        or len({review_fingerprint, corpus_fingerprint, deploy_fingerprint}) != 3
    ):
        raise BootstrapError("review, corpus, and deployment key identities are invalid")
    _verify_existing_corpus_boundary(
        corpus_public_key_file=corpus_public_key_file,
        expected_corpus_key_fingerprint=expected_corpus_key_fingerprint,
        expected_deploy_key_fingerprint=expected_deploy_key_fingerprint,
    )
    if not _account_exists():
        if check or refresh_review_code:
            raise BootstrapError("review account is missing")
        _create_account()
    _verify_account()
    if refresh_review_code:
        # Read and validate every installed managed file before creating any
        # missing review directories or replacing executable code.
        _preflight_refresh_host_files(review_key)
    for expected in DIRECTORY_CONTRACT:
        if refresh_review_code and not expected.path.exists() and not expected.path.is_symlink():
            bootstrap_dev_host._create_directory(expected)
        elif check or refresh_review_code:
            bootstrap_dev_host._verify_path(expected)
        else:
            bootstrap_dev_host._create_directory(expected)
    _ensure_host_files(
        review_key,
        apply=not check,
        refresh_review_code=refresh_review_code,
    )
    return {
        "bootstrap_sha": checkout_sha,
        "mode": "refresh-review-code" if refresh_review_code else "check" if check else "apply",
        "status": "review-observe-ready",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-key-file", required=True, type=Path)
    parser.add_argument("--expected-key-fingerprint", required=True)
    parser.add_argument("--corpus-public-key-file", required=True, type=Path)
    parser.add_argument("--expected-corpus-key-fingerprint", required=True)
    parser.add_argument("--expected-deploy-key-fingerprint", required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--refresh-review-code", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        result = bootstrap(
            public_key_file=arguments.public_key_file,
            expected_key_fingerprint=arguments.expected_key_fingerprint,
            corpus_public_key_file=arguments.corpus_public_key_file,
            expected_corpus_key_fingerprint=arguments.expected_corpus_key_fingerprint,
            expected_deploy_key_fingerprint=arguments.expected_deploy_key_fingerprint,
            check=arguments.check,
            refresh_review_code=arguments.refresh_review_code,
        )
    except (BootstrapError, OSError, ValueError):
        print("Review host bootstrap failed", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
