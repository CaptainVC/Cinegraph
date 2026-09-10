"""Wire contract for the separately authorized part-two observation boundary."""

from __future__ import annotations

import json
import re
import uuid
from typing import Final, Mapping

COMMAND: Final = "speaker-review-observe-next-primary-v1"
PROTOCOL_VERSION: Final = 1
PURPOSE: Final = "speaker_review"
SEASON_NUMBER: Final = 2
OPERATION: Final = "observe_next_primary"
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
RUN_STATUSES: Final = frozenset({"primary_submitted", "primary_part_completed", "failed"})

ENV_ARCHIVE_SHA256: Final = "CINEGRAPH_SPEAKER_REVIEW_ARCHIVE_SHA256"
ENV_RUN_ID: Final = "CINEGRAPH_SPEAKER_REVIEW_RUN_ID"
ENV_AUTHORIZATION_ID: Final = "CINEGRAPH_SPEAKER_REVIEW_AUTHORIZATION_ID"
ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: Final = (
    "CINEGRAPH_SPEAKER_REVIEW_MAXIMUM_AUTHORIZED_COST_MICROUSD"
)
ENV_EXPECTED_PRIMARY_PART_NUMBER: Final = "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_PRIMARY_PART_NUMBER"
ENV_EXPECTED_PRE_RUN_STATE_SHA256: Final = "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_PRE_RUN_STATE_SHA256"
ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: Final = (
    "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_PRE_ARTIFACT_SET_SHA256"
)
ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: Final = (
    "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_PRE_JOURNAL_SET_SHA256"
)
ENV_EXPECTED_REQUEST_SHA256: Final = "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_REQUEST_SHA256"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^speaker-review-[0-9a-f]{16}$")
_UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def canonical_json(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _decode(raw: bytes, *, maximum: int) -> dict[str, object]:
    if not isinstance(raw, bytes) or not raw or len(raw) > maximum or not raw.endswith(b"\n"):
        raise ValueError("invalid next-primary observation wire value")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("invalid next-primary observation wire value") from error
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise ValueError("invalid next-primary observation wire value")
    return value


def _run_id(value: object) -> None:
    if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
        raise ValueError("invalid run id")


def _auth_id(value: object) -> None:
    if not isinstance(value, str) or _UUID4.fullmatch(value) is None:
        raise ValueError("invalid authorization id")
    parsed = uuid.UUID(value)
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("invalid authorization id")


def _cost(value: object, *, allow_zero: bool = False) -> None:
    if (
        type(value) is not int
        or (value < 0 if allow_zero else value <= 0)
        or value > MAXIMUM_AUTHORIZED_COST_MICROUSD
    ):
        raise ValueError("invalid cost")


def validate_request(value: Mapping[str, object]) -> dict[str, object]:
    raw = canonical_json(value)
    decoded = _decode(raw, maximum=REQUEST_MAX_BYTES)
    if set(decoded) != REQUEST_KEYS or decoded.get("schema_version") != PROTOCOL_VERSION:
        raise ValueError("invalid next-primary observation request")
    if decoded.get("operation") != OPERATION or decoded.get("purpose") != PURPOSE:
        raise ValueError("invalid next-primary observation request")
    if (
        decoded.get("season_number") != SEASON_NUMBER
        or not isinstance(decoded.get("archive_sha256"), str)
        or _SHA256.fullmatch(decoded["archive_sha256"]) is None
    ):
        raise ValueError("invalid next-primary observation request")
    _run_id(decoded.get("run_id"))
    _auth_id(decoded.get("authorization_id"))
    _cost(decoded.get("maximum_authorized_cost_microusd"))
    return decoded


def parse_request(raw: bytes) -> dict[str, object]:
    return validate_request(_decode(raw, maximum=REQUEST_MAX_BYTES))


def validate_aggregate(value: object, *, status: str | None = None) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != AGGREGATE_KEYS:
        raise ValueError("invalid next-primary observation aggregate")
    expected = value.get("status") if status is None else status
    if expected not in STATUSES or value.get("status") != expected:
        raise ValueError("invalid next-primary observation aggregate")
    if (
        value.get("operation") != OPERATION
        or value.get("purpose") != PURPOSE
        or value.get("season_number") != SEASON_NUMBER
    ):
        raise ValueError("invalid next-primary observation aggregate")
    _run_id(value.get("run_id"))
    _cost(value.get("estimated_primary_cost_microusd"), allow_zero=True)
    for key in ("primary_part_count", "primary_completed_part_count"):
        if type(value.get(key)) is not int or value[key] < 0:
            raise ValueError("invalid next-primary observation aggregate")
    if (
        value["primary_part_count"] <= 1
        or value["primary_completed_part_count"] > value["primary_part_count"]
    ):
        raise ValueError("invalid next-primary observation aggregate")
    completed = value["primary_completed_part_count"]
    if expected in {"waiting", "reconciliation_required", "failed"} and completed != 1:
        raise ValueError("invalid next-primary observation aggregate")
    if expected in {"observed", "already_observed"} and completed != 2:
        raise ValueError("invalid next-primary observation aggregate")
    if (
        expected in {"waiting", "reconciliation_required"}
        and value["run_status"] != "primary_submitted"
    ):
        raise ValueError("invalid next-primary observation aggregate")
    if (
        expected in {"observed", "already_observed"}
        and value["run_status"] != "primary_part_completed"
    ):
        raise ValueError("invalid next-primary observation aggregate")
    if expected == "failed" and value["run_status"] != "failed":
        raise ValueError("invalid next-primary observation aggregate")
    return dict(value)


def parse_aggregate(raw: bytes, *, status: str | None = None) -> dict[str, object]:
    return validate_aggregate(_decode(raw, maximum=OUTPUT_MAX_BYTES), status=status)


def is_reconciliation_error(error: BaseException) -> bool:
    from cinegraph.common.error_messages import SpeakerReviewErrorMessages

    return str(error) == SpeakerReviewErrorMessages.PRIMARY_OBSERVATION_RECONCILIATION_REQUIRED


__all__ = [
    name
    for name in globals()
    if name.isupper()
    or name
    in {
        "canonical_json",
        "parse_request",
        "parse_aggregate",
        "validate_request",
        "validate_aggregate",
        "is_reconciliation_error",
    }
]
