"""Non-secret contract for the dedicated paid speaker-review submit host."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from scripts.dev_host_contract import SAFE_PATH
from scripts.private_corpus_host_contract import (
    CORPUS_USER,
    DEPLOYMENT_LOCK,
    DEV_PRIVATE_CORPUS_ROOT,
    MINIMUM_PYTHON_VERSION,
    SPEAKER_REVIEW_LOCK,
    SPEAKER_REVIEW_ROOT,
    SPEAKER_REVIEW_RUNS_ROOT,
    TRANSFER_LOCK,
)

REVIEW_USER: Final = "cinegraph-review"
REVIEW_GROUP: Final = "cinegraph-review"
REVIEW_UID: Final = 20003
REVIEW_GID: Final = 20003
REVIEW_HOME: Final = Path("/home/cinegraph-review")
REVIEW_SHELL: Final = "/bin/bash"
REVIEW_PASSWORD_FIELD: Final = "*NP*"

REVIEW_DISPATCH_PATH: Final = Path("/usr/local/libexec/cinegraph-review-dispatch")
REVIEW_HELPER_PATH: Final = Path("/usr/local/sbin/cinegraph-submit-private-speaker-review")
REVIEW_SUDOERS_PATH: Final = Path("/etc/sudoers.d/cinegraph-review")
REVIEW_AUTHORIZED_KEYS: Final = REVIEW_HOME / ".ssh/authorized_keys"
REVIEW_AUTHORIZATION_ROOT: Final = SPEAKER_REVIEW_ROOT / "authorization"
REVIEW_SUBMISSION_RECEIPTS_ROOT: Final = SPEAKER_REVIEW_ROOT / "submission-receipts"
REVIEW_COMMAND: Final = "speaker-review-submit-primary-v1"

REPOSITORY_URL: Final = "https://github.com/CaptainVC/Cinegraph.git"
RELEASES_ROOT: Final = Path("/opt/cinegraph/releases")
DEPLOY_ROOT: Final = Path("/opt/cinegraph")
SHARED_ROOT: Final = Path("/opt/cinegraph/shared")
CURRENT_LINK: Final = DEPLOY_ROOT / "current"
ENV_FILE: Final = Path("/etc/cinegraph/dev.env")
CONTAINER_NAME: Final = "cinegraph-speaker-review-submit-primary"
TIMEOUT_SECONDS: Final = 1800
KILL_AFTER_SECONDS: Final = 10
CLIENT_TIMEOUT_MARGIN_SECONDS: Final = 30
UID_IN_CONTAINER: Final = 10002
GID_IN_CONTAINER: Final = 10002

SUDOERS_CONTENT: Final = (
    f"Defaults:{REVIEW_USER} env_reset,secure_path={SAFE_PATH}\n"
    f'{REVIEW_USER} ALL=(root) NOPASSWD: {REVIEW_HELPER_PATH.as_posix()} ""\n'
)

REQUIRED_COMMANDS: Final = (
    "docker",
    "env",
    "flock",
    "git",
    "id",
    "python3",
    "readlink",
    "stat",
    "timeout",
    "uname",
)
BOOTSTRAP_COMMANDS: Final = (
    "docker",
    "getent",
    "groupadd",
    "install",
    "ssh-keygen",
    "useradd",
    "visudo",
)


def authorized_key_entry(public_key: str) -> str:
    """Build the single forced-command entry for the review identity."""

    from scripts.dev_host_contract import validate_public_key_line

    validate_public_key_line(public_key)
    return f'restrict,command="{REVIEW_DISPATCH_PATH.as_posix()}" {public_key}\n'


__all__ = [
    "BOOTSTRAP_COMMANDS",
    "CONTAINER_NAME",
    "CLIENT_TIMEOUT_MARGIN_SECONDS",
    "CORPUS_USER",
    "CURRENT_LINK",
    "DEPLOYMENT_LOCK",
    "DEV_PRIVATE_CORPUS_ROOT",
    "ENV_FILE",
    "GID_IN_CONTAINER",
    "KILL_AFTER_SECONDS",
    "MINIMUM_PYTHON_VERSION",
    "RELEASES_ROOT",
    "REPOSITORY_URL",
    "REVIEW_AUTHORIZATION_ROOT",
    "REVIEW_AUTHORIZED_KEYS",
    "REVIEW_COMMAND",
    "REVIEW_DISPATCH_PATH",
    "REVIEW_GROUP",
    "REVIEW_HELPER_PATH",
    "REVIEW_HOME",
    "REVIEW_PASSWORD_FIELD",
    "REVIEW_SHELL",
    "REVIEW_SUBMISSION_RECEIPTS_ROOT",
    "REVIEW_SUDOERS_PATH",
    "REVIEW_UID",
    "REVIEW_USER",
    "REQUIRED_COMMANDS",
    "SHARED_ROOT",
    "SPEAKER_REVIEW_LOCK",
    "SPEAKER_REVIEW_ROOT",
    "SPEAKER_REVIEW_RUNS_ROOT",
    "SUDOERS_CONTENT",
    "TIMEOUT_SECONDS",
    "TRANSFER_LOCK",
    "UID_IN_CONTAINER",
    "authorized_key_entry",
]
