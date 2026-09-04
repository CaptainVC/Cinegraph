"""Central non-secret contract for the Dev private-corpus transfer boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final, Mapping

from scripts.dev_host_contract import (
    DEPLOY_ROOT,
    SAFE_PATH,
    SHARED_ROOT,
)
from scripts.dev_host_contract import (
    RELEASES_ROOT as DEV_RELEASES_ROOT,
)

RELEASES_ROOT: Final = DEV_RELEASES_ROOT

CORPUS_USER: Final = "cinegraph-corpus"
CORPUS_GROUP: Final = "cinegraph-corpus"
CORPUS_UID: Final = 20002
CORPUS_GID: Final = 20002
CORPUS_HOME: Final = Path("/home/cinegraph-corpus")
CORPUS_SHELL: Final = "/bin/bash"
CORPUS_PASSWORD_FIELD: Final = "*NP*"

CORPUS_DISPATCH_PATH: Final = Path("/usr/local/libexec/cinegraph-corpus-dispatch")
CORPUS_HELPER_PATH: Final = Path("/usr/local/sbin/cinegraph-receive-private-corpus")
PROCESS_HELPER_PATH: Final = Path("/usr/local/sbin/cinegraph-process-private-corpus")
CORPUS_SUDOERS_PATH: Final = Path("/etc/sudoers.d/cinegraph-corpus")
CORPUS_AUTHORIZED_KEYS: Final = CORPUS_HOME / ".ssh/authorized_keys"
DEPLOY_AUTHORIZED_KEYS: Final = Path("/home/cinegraph-deploy/.ssh/authorized_keys")

PRIVATE_CORPUS_ROOT: Final = SHARED_ROOT / "private-corpus"
DEV_PRIVATE_CORPUS_ROOT: Final = PRIVATE_CORPUS_ROOT / "dev"
TRANSACTIONS_ROOT: Final = DEV_PRIVATE_CORPUS_ROOT / "transactions"
OBJECTS_ROOT: Final = DEV_PRIVATE_CORPUS_ROOT / "objects"
QUARANTINE_ROOT: Final = DEV_PRIVATE_CORPUS_ROOT / "quarantine"
PROCESSING_ROOT: Final = DEV_PRIVATE_CORPUS_ROOT / "processing"
PROCESSING_RECEIPTS_ROOT: Final = PROCESSING_ROOT / "receipts"
TRANSFER_LOCK: Final = DEV_PRIVATE_CORPUS_ROOT / ".transfer.lock"
DEPLOYMENT_LOCK: Final = DEPLOY_ROOT / ".deploy.lock"
PROCESSING_LOCK: Final = DEV_PRIVATE_CORPUS_ROOT / ".processing.lock"
CURRENT_LINK: Final = DEPLOY_ROOT / "current"
DEV_ENV_FILE: Final = Path("/etc/cinegraph/dev.env")
DEV_ENV_MAX_BYTES: Final = 64 * 1024
DEV_ENVIRONMENT_NAME: Final = "development"
CINEGRAPH_IMAGE_NAME: Final = "ghcr.io/captainvc/cinegraph"
CINEGRAPH_IMAGE_SOURCE: Final = "https://github.com/CaptainVC/Cinegraph"
CINEGRAPH_IMAGE_REVISION_LABEL: Final = "org.opencontainers.image.revision"
CINEGRAPH_IMAGE_SOURCE_LABEL: Final = "org.opencontainers.image.source"
CINEGRAPH_IMAGE_VERSION_LABEL: Final = "org.opencontainers.image.version"

RECEIVE_COMMAND: Final = "receive-v1"
PROCESS_COMMAND: Final = "process-v1"
TRANSFER_PROTOCOL_VERSION: Final = 1
INSTALL_RECEIPT_SCHEMA_VERSION: Final = 1
INSTALL_RECEIPT_FILENAME: Final = ".install-receipt.json"
HEADER_KEYS: Final = frozenset({"archive_bytes", "archive_sha256", "protocol"})
HEADER_MAX_BYTES: Final = 256
STATUS_MAX_BYTES: Final = 1024
MAX_PUBLIC_CATALOGUE_BYTES: Final = 1024 * 1024
TRANSFER_TIMEOUT_SECONDS: Final = 300
TRANSFER_KILL_AFTER_SECONDS: Final = 5
PROCESSING_TIMEOUT_SECONDS: Final = 1800
PROCESSING_KILL_AFTER_SECONDS: Final = 10
PROCESSING_OUTPUT_MAX_BYTES: Final = 16 * 1024
MINIMUM_PYTHON_VERSION: Final = (3, 12)
MIN_FREE_BYTES_AFTER_TRANSFER: Final = 1024 * 1024 * 1024
MIN_FREE_INODES_AFTER_TRANSFER: Final = 1024
TRANSFER_OVERHEAD_BYTES: Final = 1024 * 1024
TRANSACTION_PREFIX: Final = ".receive-"
OBJECT_PREFIX: Final = "sha256-"
CANONICAL_SERIES_NAME: Final = "Modern Family"
CANONICAL_SERIES_ID: Final = "00000000-0000-0000-0000-000000000011"
ALLOWED_SCHEMA_V1_SEASONS: Final = frozenset({1, 2})

CORPUS_SUDOERS_CONTENT: Final = (
    f"Defaults:{CORPUS_USER} env_reset,secure_path={SAFE_PATH}\n"
    f'{CORPUS_USER} ALL=(root) NOPASSWD: {CORPUS_HELPER_PATH.as_posix()} ""\n'
    f'{CORPUS_USER} ALL=(root) NOPASSWD: {PROCESS_HELPER_PATH.as_posix()} ""\n'
)
LEGACY_TRANSFER_ONLY_SUDOERS_CONTENT: Final = (
    f"Defaults:{CORPUS_USER} env_reset,secure_path={SAFE_PATH}\n"
    f'{CORPUS_USER} ALL=(root) NOPASSWD: {CORPUS_HELPER_PATH.as_posix()} ""\n'
)

RECEIVER_REQUIRED_COMMANDS: Final = (
    "env",
    "flock",
    "git",
    "id",
    "python3",
    "readlink",
    "stat",
    "sudo",
    "timeout",
    "uname",
)
PROCESSOR_REQUIRED_COMMANDS: Final = ("docker",)


def canonical_json(value: Mapping[str, object]) -> bytes:
    """Return the one accepted compact UTF-8 JSON representation."""

    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def corpus_authorized_key_entry(public_key: str) -> str:
    """Build the single static forced-command authorization entry."""

    from scripts.dev_host_contract import validate_public_key_line

    validate_public_key_line(public_key)
    return f'restrict,command="{CORPUS_DISPATCH_PATH.as_posix()}" {public_key}\n'
