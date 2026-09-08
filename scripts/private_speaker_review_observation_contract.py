"""Strict contract for one read-only primary Batch observation."""

from __future__ import annotations

import json
import re
import uuid
from typing import Final, Mapping

COMMAND: Final = "speaker-review-observe-primary-v1"
PROTOCOL_VERSION: Final = 1
PURPOSE: Final = "speaker_review"
SEASON_NUMBER: Final = 2
OPERATION: Final = "observe_primary"
MAXIMUM_AUTHORIZED_COST_MICROUSD: Final = 5_000_000
REQUEST_MAX_BYTES: Final = 1_024
OUTPUT_MAX_BYTES: Final = 4_096

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
        "run_status",
        "season_number",
        "status",
    }
)
STATUSES: Final = frozenset(
    {"waiting", "observed", "already_observed", "failed", "reconciliation_required"}
)
RUN_STATUSES: Final = frozenset(
    {
        "primary_submitted",
        "primary_part_completed",
        "adjudication_submitted",
        "final_review_submitted",
        "completed",
        "needs_human",
        "failed",
    }
)

ENV_ARCHIVE_SHA256: Final = "CINEGRAPH_SPEAKER_REVIEW_ARCHIVE_SHA256"
ENV_RUN_ID: Final = "CINEGRAPH_SPEAKER_REVIEW_RUN_ID"
ENV_AUTHORIZATION_ID: Final = "CINEGRAPH_SPEAKER_REVIEW_AUTHORIZATION_ID"
ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: Final = (
    "CINEGRAPH_SPEAKER_REVIEW_MAXIMUM_AUTHORIZED_COST_MICROUSD"
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^speaker-review-[0-9a-f]{16}$")
_UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def canonical_json(value: Mapping[str, object]) -> bytes:
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
        raise ValueError("invalid observation wire value")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("invalid observation wire value") from error
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise ValueError("invalid observation wire value")
    return value


def _validate_run_id(value: object) -> str:
    if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
        raise ValueError("invalid run id")
    return value


def _validate_authorization_id(value: object) -> str:
    if not isinstance(value, str) or _UUID4.fullmatch(value) is None:
        raise ValueError("invalid authorization id")
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise ValueError("invalid authorization id") from error
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("invalid authorization id")
    return value


def _validate_cost(value: object) -> int:
    if type(value) is not int or value <= 0 or value > MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise ValueError("invalid authorized cost")
    return value


def parse_request(raw: bytes) -> dict[str, object]:
    value = _decode_canonical(raw, maximum=REQUEST_MAX_BYTES)
    if set(value) != REQUEST_KEYS:
        raise ValueError("invalid observation request")
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
        raise ValueError("invalid observation request")
    _validate_run_id(value.get("run_id"))
    _validate_authorization_id(value.get("authorization_id"))
    _validate_cost(value.get("maximum_authorized_cost_microusd"))
    return value


def validate_request(value: Mapping[str, object]) -> dict[str, object]:
    return parse_request(canonical_json(value))


def _validate_estimated_cost(value: object) -> int:
    if type(value) is not int or value < 0 or value > MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise ValueError("invalid estimated cost")
    return value


def validate_aggregate(value: object, *, status: str | None = None) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != AGGREGATE_KEYS:
        raise ValueError("invalid observation aggregate")
    expected_status = value.get("status") if status is None else status
    if expected_status not in STATUSES or value.get("status") != expected_status:
        raise ValueError("invalid observation aggregate")
    if (
        value.get("operation") != OPERATION
        or value.get("purpose") != PURPOSE
        or type(value.get("season_number")) is not int
        or value["season_number"] != SEASON_NUMBER
        or value.get("run_status") not in RUN_STATUSES
    ):
        raise ValueError("invalid observation aggregate")
    _validate_run_id(value.get("run_id"))
    _validate_estimated_cost(value.get("estimated_primary_cost_microusd"))
    for key in ("primary_part_count", "primary_completed_part_count"):
        if type(value.get(key)) is not int or value[key] < 0:
            raise ValueError("invalid observation aggregate")
    if (
        value["primary_part_count"] <= 0
        or value["primary_completed_part_count"] > value["primary_part_count"]
    ):
        raise ValueError("invalid observation aggregate")
    run_status = value["run_status"]
    completed = value["primary_completed_part_count"]
    if expected_status in {"waiting", "reconciliation_required"}:
        if run_status != "primary_submitted" or completed >= value["primary_part_count"]:
            raise ValueError("invalid observation aggregate")
    elif expected_status in {"observed", "already_observed"}:
        if run_status != "primary_part_completed" or completed < 1:
            raise ValueError("invalid observation aggregate")
    elif expected_status == "failed" and run_status != "failed":
        raise ValueError("invalid observation aggregate")
    return dict(value)


def parse_aggregate(raw: bytes, *, status: str | None = None) -> dict[str, object]:
    value = _decode_canonical(raw, maximum=OUTPUT_MAX_BYTES)
    return validate_aggregate(value, status=status)


def is_reconciliation_error(error: BaseException) -> bool:
    from cinegraph.common.error_messages import SpeakerReviewErrorMessages

    return str(error) == SpeakerReviewErrorMessages.PRIMARY_OBSERVATION_RECONCILIATION_REQUIRED


__all__ = [
    "AGGREGATE_KEYS",
    "COMMAND",
    "ENV_ARCHIVE_SHA256",
    "ENV_AUTHORIZATION_ID",
    "ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD",
    "ENV_RUN_ID",
    "MAXIMUM_AUTHORIZED_COST_MICROUSD",
    "OPERATION",
    "OUTPUT_MAX_BYTES",
    "PROTOCOL_VERSION",
    "PURPOSE",
    "REQUEST_KEYS",
    "REQUEST_MAX_BYTES",
    "RUN_STATUSES",
    "SEASON_NUMBER",
    "STATUSES",
    "canonical_json",
    "is_reconciliation_error",
    "parse_aggregate",
    "parse_request",
    "validate_aggregate",
    "validate_request",
]
