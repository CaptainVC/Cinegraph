"""Small, dependency-free wire and filesystem contract for corpus processing.

The transfer boundary deliberately has its own contract.  This module is kept
stdlib-only so the operator client can validate and construct the wire request
without importing the application runtime.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Final, Mapping

from scripts import private_corpus_host_contract as host_contract

PROCESS_COMMAND: Final = "process-v1"
PROCESSING_COMMAND: Final = PROCESS_COMMAND
PROCESS_PROTOCOL_VERSION: Final = 1
PROCESS_REQUEST_KEYS: Final = frozenset(
    {"archive_sha256", "operation", "purpose", "schema_version", "season_number"}
)
PROCESS_OPERATIONS: Final = frozenset({"validate", "ingest-reviewed"})
PROCESS_STATUSES_BY_OPERATION: Final = {
    "validate": frozenset({"validated"}),
    "ingest-reviewed": frozenset({"applied", "already_applied"}),
}
PROCESS_PURPOSE: Final = "reviewed_ingestion"
PROCESS_SEASON_NUMBER: Final = 1
PROCESS_REQUEST_MAX_BYTES: Final = 512
PROCESS_STATUS_MAX_BYTES: Final = getattr(host_contract, "PROCESSING_OUTPUT_MAX_BYTES", 16 * 1024)
PROCESS_OUTPUT_MAX_BYTES: Final = PROCESS_STATUS_MAX_BYTES
PROCESSING_OUTPUT_MAX_BYTES: Final = PROCESS_OUTPUT_MAX_BYTES
PROCESSING_TIMEOUT_SECONDS: Final = getattr(host_contract, "PROCESSING_TIMEOUT_SECONDS", 1800)
PROCESSING_KILL_AFTER_SECONDS: Final = getattr(host_contract, "PROCESSING_KILL_AFTER_SECONDS", 10)
PROCESSING_WORKER_SHUTDOWN_SECONDS: Final = 60
PROCESSING_WORKER_TIMEOUT_SECONDS: Final = (
    PROCESSING_TIMEOUT_SECONDS - PROCESSING_WORKER_SHUTDOWN_SECONDS
)
PROCESSING_CLIENT_TIMEOUT_MARGIN_SECONDS: Final = 30
PROCESSING_SSH_CONNECTION_ATTEMPTS: Final = 3
PROCESSING_SSH_CONNECT_TIMEOUT_SECONDS: Final = 10
PROCESSING_SSH_SERVER_ALIVE_INTERVAL_SECONDS: Final = 15
PROCESSING_SSH_SERVER_ALIVE_COUNT_MAX: Final = 2

PROCESSING_UID: Final = 10001
PROCESSING_GID: Final = 10001
PROCESSING_ROOT: Final = getattr(
    host_contract,
    "PROCESSING_ROOT",
    host_contract.DEV_PRIVATE_CORPUS_ROOT / "processing",
)
PROCESSING_WORKSPACE_ROOT: Final = PROCESSING_ROOT
PROCESSING_WORKSPACE_PREFIX: Final = "sha256-"
PROCESSING_STAGING_PREFIX: Final = ".process-"
PROCESSING_RECEIPTS_ROOT: Final = getattr(
    host_contract, "PROCESSING_RECEIPTS_ROOT", PROCESSING_ROOT / "receipts"
)
PROCESSING_LOCK: Final = getattr(
    host_contract,
    "PROCESSING_LOCK",
    host_contract.DEV_PRIVATE_CORPUS_ROOT / ".processing.lock",
)
PROCESSING_RECEIPT_SCHEMA_VERSION: Final = 1
PROCESSING_RECEIPT_PREFIX: Final = "sha256-"

WORKER_KEYS: Final = frozenset(
    {
        "episode_count",
        "file_count",
        "indexed_segment_count",
        "mode",
        "purpose",
        "season_number",
        "total_bytes",
    }
)
AGGREGATE_KEYS: Final = frozenset(
    {
        "episode_count",
        "file_count",
        "indexed_segment_count",
        "mode",
        "purpose",
        "season_number",
        "status",
        "total_bytes",
    }
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def canonical_json(value: Mapping[str, object]) -> bytes:
    """Encode one compact, newline-terminated canonical JSON object."""

    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def parse_request(raw: bytes) -> dict[str, object]:
    """Parse exactly one bounded canonical processing request."""

    if (
        not raw
        or len(raw) > PROCESS_REQUEST_MAX_BYTES
        or not raw.endswith(b"\n")
        or raw.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"))
    ):
        raise ValueError("invalid request")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("invalid request") from error
    if not isinstance(value, dict) or set(value) != PROCESS_REQUEST_KEYS:
        raise ValueError("invalid request")
    if canonical_json(value) != raw:
        raise ValueError("invalid request")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != PROCESS_PROTOCOL_VERSION
        or not isinstance(value["archive_sha256"], str)
        or _SHA256.fullmatch(value["archive_sha256"]) is None
        or not isinstance(value["operation"], str)
        or value["operation"] not in PROCESS_OPERATIONS
        or not isinstance(value["purpose"], str)
        or value["purpose"] != PROCESS_PURPOSE
        or type(value["season_number"]) is not int
        or value["season_number"] != PROCESS_SEASON_NUMBER
    ):
        raise ValueError("invalid request")
    return value


def workspace_for(archive_sha256: str) -> Path:
    if _SHA256.fullmatch(archive_sha256) is None:
        raise ValueError("invalid digest")
    return PROCESSING_ROOT / f"{PROCESSING_WORKSPACE_PREFIX}{archive_sha256}"


def receipt_for(archive_sha256: str) -> Path:
    if _SHA256.fullmatch(archive_sha256) is None:
        raise ValueError("invalid digest")
    return PROCESSING_RECEIPTS_ROOT / f"{PROCESSING_RECEIPT_PREFIX}{archive_sha256}.json"


def validate_aggregate(value: object, *, mode: str, status: str) -> dict[str, object]:
    """Validate and normalize the public aggregate emitted by the root helper."""

    if not isinstance(value, dict) or set(value) != AGGREGATE_KEYS:
        raise ValueError("invalid aggregate")
    if status not in PROCESS_STATUSES_BY_OPERATION.get(mode, frozenset()):
        raise ValueError("invalid aggregate")
    if value.get("status") != status or value.get("mode") != mode:
        raise ValueError("invalid aggregate")
    if value.get("purpose") != PROCESS_PURPOSE or value.get("season_number") != 1:
        raise ValueError("invalid aggregate")
    for key in ("file_count", "total_bytes", "episode_count", "indexed_segment_count"):
        if type(value.get(key)) is not int or value[key] < 0:
            raise ValueError("invalid aggregate")
    if value["file_count"] <= 0 or value["total_bytes"] <= 0:
        raise ValueError("invalid aggregate")
    if value["episode_count"] > value["file_count"]:
        raise ValueError("invalid aggregate")
    return dict(value)
