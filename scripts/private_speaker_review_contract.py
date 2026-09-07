"""Stdlib-only wire contract for the private speaker-review worker.

The speaker-review boundary is deliberately separate from reviewed corpus
ingestion.  It accepts only an archive digest and a small operator command;
the archive itself is installed on the VPS by the private transfer boundary.
No provider request, subtitle text, path, or secret is part of this protocol.
"""

from __future__ import annotations

import json
import math
import re
from typing import Final, Mapping

from scripts import private_corpus_host_contract as host_contract

SPEAKER_REVIEW_COMMAND: Final = host_contract.SPEAKER_REVIEW_COMMAND
SPEAKER_REVIEW_PROTOCOL_VERSION: Final = 1
SPEAKER_REVIEW_PURPOSE: Final = "speaker_review"
SPEAKER_REVIEW_SEASON_NUMBER: Final = 2
SPEAKER_REVIEW_OPERATIONS: Final = frozenset({"validate", "prepare", "status"})
SPEAKER_REVIEW_PAID_OPERATIONS: Final = frozenset(
    {"submit", "advance", "apply", "ingest", "finalize"}
)
SPEAKER_REVIEW_REQUEST_KEYS: Final = frozenset(
    {"archive_sha256", "operation", "purpose", "schema_version", "season_number"}
)
SPEAKER_REVIEW_STATUS_REQUEST_KEYS: Final = SPEAKER_REVIEW_REQUEST_KEYS | {"run_id"}
SPEAKER_REVIEW_REQUEST_MAX_BYTES: Final = 512
SPEAKER_REVIEW_STATUS_MAX_BYTES: Final = getattr(
    host_contract,
    "SPEAKER_REVIEW_OUTPUT_MAX_BYTES",
    getattr(host_contract, "PROCESSING_OUTPUT_MAX_BYTES", 16 * 1024),
)
SPEAKER_REVIEW_OUTPUT_MAX_BYTES: Final = SPEAKER_REVIEW_STATUS_MAX_BYTES
SPEAKER_REVIEW_TIMEOUT_SECONDS: Final = getattr(
    host_contract,
    "SPEAKER_REVIEW_TIMEOUT_SECONDS",
    getattr(host_contract, "PROCESSING_TIMEOUT_SECONDS", 1800),
)
SPEAKER_REVIEW_KILL_AFTER_SECONDS: Final = getattr(
    host_contract,
    "SPEAKER_REVIEW_KILL_AFTER_SECONDS",
    getattr(host_contract, "PROCESSING_KILL_AFTER_SECONDS", 10),
)
SPEAKER_REVIEW_CLIENT_TIMEOUT_MARGIN_SECONDS: Final = 30
SPEAKER_REVIEW_SSH_CONNECTION_ATTEMPTS: Final = 3
SPEAKER_REVIEW_SSH_CONNECT_TIMEOUT_SECONDS: Final = 10
SPEAKER_REVIEW_SSH_SERVER_ALIVE_INTERVAL_SECONDS: Final = 15
SPEAKER_REVIEW_SSH_SERVER_ALIVE_COUNT_MAX: Final = 2

SPEAKER_REVIEW_AGGREGATE_KEYS: Final = frozenset(
    {
        "operation",
        "purpose",
        "season_number",
        "status",
        "file_count",
        "total_bytes",
        "candidate_count",
        "primary_part_count",
        "estimated_primary_cost_usd",
        "run_id",
    }
)
SPEAKER_REVIEW_VALIDATE_AGGREGATE_KEYS: Final = frozenset(
    {"operation", "purpose", "season_number", "status", "file_count", "total_bytes"}
)
SPEAKER_REVIEW_PREPARE_AGGREGATE_KEYS: Final = SPEAKER_REVIEW_AGGREGATE_KEYS
SPEAKER_REVIEW_STATUS_AGGREGATE_KEYS: Final = SPEAKER_REVIEW_AGGREGATE_KEYS
SPEAKER_REVIEW_STATUSES_BY_OPERATION: Final = {
    "validate": frozenset({"validated"}),
    "prepare": frozenset({"prepared", "already_prepared"}),
    "status": frozenset({"prepared", "already_prepared"}),
}

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^speaker-review-[0-9a-f]{16}$")


def canonical_json(value: Mapping[str, object]) -> bytes:
    """Encode one compact, sorted, ASCII JSON object with exactly one LF."""

    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _decode_canonical(raw: bytes, *, maximum: int) -> dict[str, object]:
    if (
        not raw
        or len(raw) > maximum
        or not raw.endswith(b"\n")
        or raw.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"))
    ):
        raise ValueError("invalid speaker review wire value")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("invalid speaker review wire value") from error
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise ValueError("invalid speaker review wire value")
    return value


def _validate_run_id(value: object) -> str:
    if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
        raise ValueError("invalid run id")
    return value


def parse_request(raw: bytes) -> dict[str, object]:
    """Parse a request with strict duplicate, key, type and EOF validation."""

    value = _decode_canonical(raw, maximum=SPEAKER_REVIEW_REQUEST_MAX_BYTES)
    operation = value.get("operation")
    expected_keys = (
        SPEAKER_REVIEW_STATUS_REQUEST_KEYS if operation == "status" else SPEAKER_REVIEW_REQUEST_KEYS
    )
    if set(value) != expected_keys:
        raise ValueError("invalid speaker review request")
    archive_sha256 = value.get("archive_sha256")
    purpose = value.get("purpose")
    season_number = value.get("season_number")
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != SPEAKER_REVIEW_PROTOCOL_VERSION
        or not isinstance(archive_sha256, str)
        or _SHA256.fullmatch(archive_sha256) is None
        or not isinstance(operation, str)
        or operation not in SPEAKER_REVIEW_OPERATIONS
        or not isinstance(purpose, str)
        or purpose != SPEAKER_REVIEW_PURPOSE
        or type(season_number) is not int
        or season_number != SPEAKER_REVIEW_SEASON_NUMBER
    ):
        raise ValueError("invalid speaker review request")
    if operation == "status":
        _validate_run_id(value.get("run_id"))
    return value


def validate_request(value: Mapping[str, object]) -> dict[str, object]:
    """Validate an in-memory request using the same canonical contract."""

    return parse_request(canonical_json(value))


def validate_aggregate(
    value: object,
    *,
    operation: str | None = None,
    status: str | None = None,
    mode: str | None = None,
) -> dict[str, object]:
    """Validate the public aggregate and reject provider/private fields.

    ``mode`` is accepted as a compatibility spelling for callers that use the
    reviewed-ingestion contract's terminology.
    """

    expected_operation = operation if operation is not None else mode
    if expected_operation is None and isinstance(value, dict):
        expected_operation = value.get("operation")
    expected_keys = (
        SPEAKER_REVIEW_VALIDATE_AGGREGATE_KEYS
        if expected_operation == "validate"
        else SPEAKER_REVIEW_AGGREGATE_KEYS
    )
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ValueError("invalid speaker review aggregate")
    if expected_operation not in SPEAKER_REVIEW_OPERATIONS:
        raise ValueError("invalid speaker review aggregate")
    expected_status = status if status is not None else value.get("status")
    if expected_status not in SPEAKER_REVIEW_STATUSES_BY_OPERATION[expected_operation]:
        raise ValueError("invalid speaker review aggregate")
    if value.get("operation") != expected_operation or value.get("status") != expected_status:
        raise ValueError("invalid speaker review aggregate")
    if (
        value.get("purpose") != SPEAKER_REVIEW_PURPOSE
        or type(value.get("season_number")) is not int
        or value["season_number"] != SPEAKER_REVIEW_SEASON_NUMBER
    ):
        raise ValueError("invalid speaker review aggregate")
    for key in ("file_count", "total_bytes"):
        if type(value.get(key)) is not int or value[key] < 0:
            raise ValueError("invalid speaker review aggregate")
    if value["file_count"] <= 0 or value["total_bytes"] <= 0:
        raise ValueError("invalid speaker review aggregate")
    if expected_operation == "validate":
        return dict(value)
    for key in ("candidate_count", "primary_part_count"):
        if type(value.get(key)) is not int or value[key] < 0:
            raise ValueError("invalid speaker review aggregate")
    if value["candidate_count"] <= 0 or value["primary_part_count"] <= 0:
        raise ValueError("invalid speaker review aggregate")
    _validate_run_id(value.get("run_id"))
    if type(value.get("estimated_primary_cost_usd")) not in (int, float):
        raise ValueError("invalid speaker review aggregate")
    cost = value["estimated_primary_cost_usd"]
    if isinstance(cost, bool) or not math.isfinite(float(cost)) or cost < 0:
        raise ValueError("invalid speaker review aggregate")
    return dict(value)


def parse_aggregate(raw: bytes, *, operation: str | None = None) -> dict[str, object]:
    """Parse one bounded canonical aggregate emitted by the VPS worker."""

    value = _decode_canonical(raw, maximum=SPEAKER_REVIEW_OUTPUT_MAX_BYTES)
    return validate_aggregate(value, operation=operation)


def is_paid_operation(operation: object) -> bool:
    """Return whether an operation is provider-mutating/paid and forbidden here."""

    return isinstance(operation, str) and operation in SPEAKER_REVIEW_PAID_OPERATIONS


# Stable short names for the root worker/host integration.  The descriptive
# names above remain the source of truth; aliases keep call sites readable
# without duplicating policy literals.
REVIEW_COMMAND: Final = SPEAKER_REVIEW_COMMAND
REVIEW_PROTOCOL_VERSION: Final = SPEAKER_REVIEW_PROTOCOL_VERSION
REVIEW_PURPOSE: Final = SPEAKER_REVIEW_PURPOSE
REVIEW_SEASON_NUMBER: Final = SPEAKER_REVIEW_SEASON_NUMBER
REVIEW_REQUEST_MAX_BYTES: Final = SPEAKER_REVIEW_REQUEST_MAX_BYTES
REVIEW_OUTPUT_MAX_BYTES: Final = SPEAKER_REVIEW_OUTPUT_MAX_BYTES
REVIEW_REQUEST_KEYS: Final = SPEAKER_REVIEW_REQUEST_KEYS
REVIEW_STATUS_REQUEST_KEYS: Final = SPEAKER_REVIEW_STATUS_REQUEST_KEYS
REVIEW_OPERATIONS: Final = SPEAKER_REVIEW_OPERATIONS
REVIEW_WORKER_KEYS: Final = SPEAKER_REVIEW_AGGREGATE_KEYS
REVIEW_AGGREGATE_KEYS: Final = SPEAKER_REVIEW_AGGREGATE_KEYS
REVIEW_VALIDATE_AGGREGATE_KEYS: Final = SPEAKER_REVIEW_VALIDATE_AGGREGATE_KEYS
REVIEW_PREPARE_AGGREGATE_KEYS: Final = SPEAKER_REVIEW_PREPARE_AGGREGATE_KEYS
REVIEW_STATUS_AGGREGATE_KEYS: Final = SPEAKER_REVIEW_STATUS_AGGREGATE_KEYS
