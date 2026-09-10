"""Exact contract for one internal next-primary-part submission."""

from __future__ import annotations

import json
import re
import uuid
from typing import Final, Mapping

COMMAND: Final = "speaker-review-submit-next-primary-v1"
PROTOCOL_VERSION: Final = 1
PURPOSE: Final = "speaker_review"
SEASON_NUMBER: Final = 2
OPERATION: Final = "submit_next_primary"
REQUEST_MAX_BYTES: Final = 1_024
OUTPUT_MAX_BYTES: Final = 4_096
MAXIMUM_AUTHORIZED_COST_MICROUSD: Final = 5_000_000

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
        "primary_completed_part_count",
        "primary_part_count",
        "purpose",
        "run_id",
        "season_number",
        "status",
        "submitted_part_count",
    }
)
STATUSES: Final = frozenset(
    {"submitted", "already_submitted", "all_parts_completed", "reconciliation_required"}
)

ENV_ARCHIVE_SHA256: Final = "CINEGRAPH_SPEAKER_REVIEW_ARCHIVE_SHA256"
ENV_AUTHORIZATION_ID: Final = "CINEGRAPH_SPEAKER_REVIEW_AUTHORIZATION_ID"
ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: Final = (
    "CINEGRAPH_SPEAKER_REVIEW_MAXIMUM_AUTHORIZED_COST_MICROUSD"
)
ENV_RUN_ID: Final = "CINEGRAPH_SPEAKER_REVIEW_RUN_ID"
ENV_EXPECTED_REQUEST_SHA256: Final = "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_REQUEST_SHA256"
ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: Final = (
    "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_PRE_ARTIFACT_SET_SHA256"
)
ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: Final = (
    "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_PRE_JOURNAL_SET_SHA256"
)
ENV_EXPECTED_PRE_RUN_STATE_SHA256: Final = "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_PRE_RUN_STATE_SHA256"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^speaker-review-[0-9a-f]{16}$")
_UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def canonical_json(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, item in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = item
    return result


def _decode(raw: bytes, *, maximum: int) -> dict[str, object]:
    if (
        not isinstance(raw, bytes)
        or not raw
        or len(raw) > maximum
        or not raw.endswith(b"\n")
        or raw.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"))
    ):
        raise ValueError("invalid next-primary wire value")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("invalid next-primary wire value") from error
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise ValueError("invalid next-primary wire value")
    return value


def _run_id(value: object) -> str:
    if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
        raise ValueError("invalid run id")
    return value


def _authorization_id(value: object) -> str:
    if not isinstance(value, str) or _UUID4.fullmatch(value) is None:
        raise ValueError("invalid authorization id")
    parsed = uuid.UUID(value)
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("invalid authorization id")
    return value


def _cost_cap(value: object) -> int:
    if type(value) is not int or value <= 0 or value > MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise ValueError("invalid authorized cost")
    return value


def validate_request(value: Mapping[str, object]) -> dict[str, object]:
    raw = canonical_json(value)
    decoded = _decode(raw, maximum=REQUEST_MAX_BYTES)
    if set(decoded) != REQUEST_KEYS:
        raise ValueError("invalid next-primary request")
    if (
        type(decoded.get("schema_version")) is not int
        or decoded["schema_version"] != PROTOCOL_VERSION
        or decoded.get("operation") != OPERATION
        or decoded.get("purpose") != PURPOSE
        or type(decoded.get("season_number")) is not int
        or decoded["season_number"] != SEASON_NUMBER
        or not isinstance(decoded.get("archive_sha256"), str)
        or _SHA256.fullmatch(decoded["archive_sha256"]) is None
    ):
        raise ValueError("invalid next-primary request")
    _run_id(decoded.get("run_id"))
    _authorization_id(decoded.get("authorization_id"))
    _cost_cap(decoded.get("maximum_authorized_cost_microusd"))
    return decoded


def parse_request(raw: bytes) -> dict[str, object]:
    return validate_request(_decode(raw, maximum=REQUEST_MAX_BYTES))


def _cost(value: object) -> int:
    if type(value) is not int or value < 0 or value > MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise ValueError("invalid estimated cost")
    return value


def validate_aggregate(value: object, *, status: str | None = None) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != AGGREGATE_KEYS:
        raise ValueError("invalid next-primary aggregate")
    expected = value.get("status") if status is None else status
    if expected not in STATUSES or value.get("status") != expected:
        raise ValueError("invalid next-primary aggregate")
    if (
        value.get("operation") != OPERATION
        or value.get("purpose") != PURPOSE
        or type(value.get("season_number")) is not int
        or value["season_number"] != SEASON_NUMBER
    ):
        raise ValueError("invalid next-primary aggregate")
    _run_id(value.get("run_id"))
    _cost(value.get("estimated_primary_cost_microusd"))
    for key in ("primary_part_count", "primary_completed_part_count", "submitted_part_count"):
        if type(value.get(key)) is not int or value[key] < 0:
            raise ValueError("invalid next-primary aggregate")
    if (
        value["primary_part_count"] <= 0
        or value["primary_completed_part_count"] > value["primary_part_count"]
    ):
        raise ValueError("invalid next-primary aggregate")
    if expected in {"submitted", "already_submitted"} and value["submitted_part_count"] != 1:
        raise ValueError("invalid next-primary aggregate")
    if (
        expected in {"all_parts_completed", "reconciliation_required"}
        and value["submitted_part_count"] != 0
    ):
        raise ValueError("invalid next-primary aggregate")
    if (
        expected == "all_parts_completed"
        and value["primary_completed_part_count"] != value["primary_part_count"]
    ):
        raise ValueError("invalid next-primary aggregate")
    if expected in {"submitted", "already_submitted", "reconciliation_required"} and not (
        0 < value["primary_completed_part_count"] < value["primary_part_count"]
    ):
        raise ValueError("invalid next-primary aggregate")
    return dict(value)


def parse_aggregate(raw: bytes, *, status: str | None = None) -> dict[str, object]:
    return validate_aggregate(_decode(raw, maximum=OUTPUT_MAX_BYTES), status=status)


def is_reconciliation_error(error: BaseException) -> bool:
    from cinegraph.common.error_messages import SpeakerReviewErrorMessages

    return str(error) in {
        SpeakerReviewErrorMessages.NEXT_PRIMARY_SUBMISSION_RECONCILIATION_REQUIRED,
        SpeakerReviewErrorMessages.BATCH_SUBMISSION_RECONCILIATION_REQUIRED,
    }


__all__ = [
    "AGGREGATE_KEYS",
    "COMMAND",
    "ENV_ARCHIVE_SHA256",
    "ENV_AUTHORIZATION_ID",
    "ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD",
    "ENV_RUN_ID",
    "ENV_EXPECTED_REQUEST_SHA256",
    "ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256",
    "ENV_EXPECTED_PRE_JOURNAL_SET_SHA256",
    "ENV_EXPECTED_PRE_RUN_STATE_SHA256",
    "MAXIMUM_AUTHORIZED_COST_MICROUSD",
    "OPERATION",
    "OUTPUT_MAX_BYTES",
    "PROTOCOL_VERSION",
    "PURPOSE",
    "REQUEST_KEYS",
    "REQUEST_MAX_BYTES",
    "SEASON_NUMBER",
    "STATUSES",
    "canonical_json",
    "is_reconciliation_error",
    "parse_aggregate",
    "parse_request",
    "validate_aggregate",
    "validate_request",
]
