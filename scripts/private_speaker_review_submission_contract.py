"""Strict contract for the paid primary speaker-review submission boundary.

This protocol is intentionally separate from the offline preparation protocol.
It authorizes exactly one primary Batch submission and never carries subtitle
content, request payloads, provider identifiers, paths, or secrets.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Final, Mapping

COMMAND: Final = "speaker-review-submit-primary-v1"
SUBMISSION_COMMAND: Final = COMMAND
PROTOCOL_VERSION: Final = 1
PURPOSE: Final = "speaker_review"
SEASON_NUMBER: Final = 2
OPERATION: Final = "submit_primary"
MAXIMUM_AUTHORIZED_COST_MICROUSD: Final = 5_000_000
REQUEST_MAX_BYTES: Final = 1_024
OUTPUT_MAX_BYTES: Final = 4_096
SSH_CONNECTION_ATTEMPTS: Final = 3
SSH_CONNECT_TIMEOUT_SECONDS: Final = 10
SSH_SERVER_ALIVE_INTERVAL_SECONDS: Final = 15
SSH_SERVER_ALIVE_COUNT_MAX: Final = 2

REQUEST_KEYS: Final = frozenset(
    {
        "archive_sha256",
        "authorization_id",
        "maximum_authorized_cost_microusd",
        "operation",
        "purpose",
        "run_id",
        "schema_version",
        "season_number",
    }
)
AGGREGATE_KEYS: Final = frozenset(
    {
        "estimated_primary_cost_microusd",
        "operation",
        "primary_part_count",
        "purpose",
        "run_id",
        "season_number",
        "status",
        "submitted_part_count",
    }
)
STATUSES: Final = frozenset({"submitted", "already_submitted", "reconciliation_required"})

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^speaker-review-[0-9a-f]{16}$")
_UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

# Fixed names are exported so the container and workstation client cannot
# drift into accepting an alternate environment variable protocol.
ENV_ARCHIVE_SHA256: Final = "CINEGRAPH_SPEAKER_REVIEW_ARCHIVE_SHA256"
ENV_RUN_ID: Final = "CINEGRAPH_SPEAKER_REVIEW_RUN_ID"
ENV_AUTHORIZATION_ID: Final = "CINEGRAPH_SPEAKER_REVIEW_AUTHORIZATION_ID"
ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: Final = (
    "CINEGRAPH_SPEAKER_REVIEW_MAXIMUM_AUTHORIZED_COST_MICROUSD"
)


def canonical_json(value: Mapping[str, object]) -> bytes:
    """Encode one compact, sorted, ASCII JSON object with exactly one LF."""

    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _decode_canonical(raw: bytes, *, maximum: int) -> dict[str, object]:
    if (
        not isinstance(raw, bytes)
        or not raw
        or len(raw) > maximum
        or not raw.endswith(b"\n")
        or raw.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"))
    ):
        raise ValueError("invalid submission wire value")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("invalid submission wire value") from error
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise ValueError("invalid submission wire value")
    return value


def _run_id(value: object) -> str:
    if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
        raise ValueError("invalid run id")
    return value


def _authorization_id(value: object) -> str:
    if not isinstance(value, str) or _UUID4.fullmatch(value) is None:
        raise ValueError("invalid authorization id")
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise ValueError("invalid authorization id") from error
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("invalid authorization id")
    return value


def _cost_cap(value: object) -> int:
    if type(value) is not int or value <= 0 or value > MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise ValueError("invalid authorized cost")
    return value


def parse_request(raw: bytes) -> dict[str, object]:
    value = _decode_canonical(raw, maximum=REQUEST_MAX_BYTES)
    if set(value) != REQUEST_KEYS:
        raise ValueError("invalid submission request")
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != PROTOCOL_VERSION
        or value.get("operation") != OPERATION
        or value.get("purpose") != PURPOSE
        or type(value.get("season_number")) is not int
        or value["season_number"] != SEASON_NUMBER
        or not isinstance(value.get("archive_sha256"), str)
        or _SHA256.fullmatch(value["archive_sha256"]) is None
    ):
        raise ValueError("invalid submission request")
    _run_id(value.get("run_id"))
    _authorization_id(value.get("authorization_id"))
    _cost_cap(value.get("maximum_authorized_cost_microusd"))
    return value


def validate_request(value: Mapping[str, object]) -> dict[str, object]:
    return parse_request(canonical_json(value))


def _estimated_cost(value: object) -> int:
    if type(value) is not int or value < 0 or value > MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise ValueError("invalid estimated cost")
    return value


def validate_aggregate(value: object, *, status: str | None = None) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != AGGREGATE_KEYS:
        raise ValueError("invalid submission aggregate")
    expected_status = value.get("status") if status is None else status
    if expected_status not in STATUSES or value.get("status") != expected_status:
        raise ValueError("invalid submission aggregate")
    if (
        value.get("operation") != OPERATION
        or value.get("purpose") != PURPOSE
        or type(value.get("season_number")) is not int
        or value["season_number"] != SEASON_NUMBER
    ):
        raise ValueError("invalid submission aggregate")
    _run_id(value.get("run_id"))
    _estimated_cost(value.get("estimated_primary_cost_microusd"))
    for key in ("primary_part_count", "submitted_part_count"):
        if type(value.get(key)) is not int or value[key] < 0:
            raise ValueError("invalid submission aggregate")
    if value["primary_part_count"] <= 0:
        raise ValueError("invalid submission aggregate")
    if expected_status in {"submitted", "already_submitted"}:
        if value["submitted_part_count"] != 1:
            raise ValueError("invalid submission aggregate")
    elif value["submitted_part_count"] != 0:
        raise ValueError("invalid submission aggregate")
    return dict(value)


def parse_aggregate(raw: bytes, *, status: str | None = None) -> dict[str, object]:
    value = _decode_canonical(raw, maximum=OUTPUT_MAX_BYTES)
    return validate_aggregate(value, status=status)


def is_reconciliation_error(error: BaseException) -> bool:
    """Recognize only the workflow's exact no-duplicate-submit error."""

    from cinegraph.common.error_messages import SpeakerReviewErrorMessages

    return str(error) == SpeakerReviewErrorMessages.BATCH_SUBMISSION_RECONCILIATION_REQUIRED


# Compatibility aliases make the contract easy to consume from small scripts.
SUBMISSION_PROTOCOL_VERSION: Final = PROTOCOL_VERSION
SUBMISSION_PURPOSE: Final = PURPOSE
SUBMISSION_SEASON_NUMBER: Final = SEASON_NUMBER
SUBMISSION_OPERATION: Final = OPERATION
