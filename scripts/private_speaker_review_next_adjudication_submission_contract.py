"""Strict wire contract for one subsequent private adjudication submission."""

from __future__ import annotations

import json
import re
import uuid
from typing import Final, Mapping

COMMAND: Final = "speaker-review-submit-next-adjudication-v1"
PROTOCOL_VERSION: Final = 1
PURPOSE: Final = "speaker_review"
SEASON_NUMBER: Final = 2
OPERATION: Final = "submit_next_adjudication"
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
        "actual_primary_cost_microusd",
        "adjudication_completed_part_count",
        "adjudication_part_count",
        "estimated_adjudication_cost_microusd",
        "operation",
        "purpose",
        "run_id",
        "run_status",
        "season_number",
        "status",
        "submitted_part_count",
    }
)
STATUSES: Final = frozenset({"submitted", "already_submitted", "reconciliation_required"})
RUN_STATUSES: Final = frozenset(
    {"adjudication_part_completed", "adjudication_submitted"}
)

ENV_ARCHIVE_SHA256: Final = "CINEGRAPH_SPEAKER_REVIEW_ARCHIVE_SHA256"
ENV_RUN_ID: Final = "CINEGRAPH_SPEAKER_REVIEW_RUN_ID"
ENV_AUTHORIZATION_ID: Final = "CINEGRAPH_SPEAKER_REVIEW_AUTHORIZATION_ID"
ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: Final = (
    "CINEGRAPH_SPEAKER_REVIEW_MAXIMUM_AUTHORIZED_COST_MICROUSD"
)
ENV_EXPECTED_PRE_RUN_STATE_SHA256: Final = "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_PRE_RUN_STATE_SHA256"
ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: Final = (
    "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_PRE_ARTIFACT_SET_SHA256"
)
ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: Final = (
    "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_PRE_JOURNAL_SET_SHA256"
)
ENV_EXPECTED_PRE_OUTPUT_SET_SHA256: Final = (
    "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_PRE_OUTPUT_SET_SHA256"
)
ENV_EXPECTED_PRE_DERIVED_SET_SHA256: Final = (
    "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_PRE_DERIVED_SET_SHA256"
)
ENV_EXPECTED_REQUEST_SHA256: Final = "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_REQUEST_SHA256"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^speaker-review-[0-9a-f]{16}$")
_UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def canonical_json(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _decode(raw: bytes, *, maximum: int) -> dict[str, object]:
    if (
        not isinstance(raw, bytes)
        or not raw
        or len(raw) > maximum
        or not raw.endswith(b"\n")
        or raw.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"))
    ):
        raise ValueError("invalid next-adjudication wire value")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("invalid next-adjudication wire value") from error
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise ValueError("invalid next-adjudication wire value")
    return value


def _run_id(value: object) -> None:
    if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
        raise ValueError("invalid run id")


def _authorization_id(value: object) -> None:
    if not isinstance(value, str) or _UUID4.fullmatch(value) is None:
        raise ValueError("invalid authorization id")
    parsed = uuid.UUID(value)
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("invalid authorization id")


def _cost(value: object, *, positive: bool) -> None:
    if (
        type(value) is not int
        or (value <= 0 if positive else value < 0)
        or value > MAXIMUM_AUTHORIZED_COST_MICROUSD
    ):
        raise ValueError("invalid cost")


def _digest(value: object) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError("invalid digest")


def validate_request(value: Mapping[str, object]) -> dict[str, object]:
    decoded = _decode(canonical_json(value), maximum=REQUEST_MAX_BYTES)
    if (
        set(decoded) != REQUEST_KEYS
        or type(decoded.get("schema_version")) is not int
        or decoded.get("schema_version") != PROTOCOL_VERSION
        or decoded.get("operation") != OPERATION
        or decoded.get("purpose") != PURPOSE
        or type(decoded.get("season_number")) is not int
        or decoded.get("season_number") != SEASON_NUMBER
        or not isinstance(decoded.get("archive_sha256"), str)
        or _SHA256.fullmatch(str(decoded["archive_sha256"])) is None
    ):
        raise ValueError("invalid next-adjudication request")
    _run_id(decoded.get("run_id"))
    _authorization_id(decoded.get("authorization_id"))
    _cost(decoded.get("maximum_authorized_cost_microusd"), positive=True)
    return decoded


def parse_request(raw: bytes) -> dict[str, object]:
    return validate_request(_decode(raw, maximum=REQUEST_MAX_BYTES))


def validate_aggregate(value: object, *, status: str | None = None) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != AGGREGATE_KEYS:
        raise ValueError("invalid next-adjudication aggregate")
    expected = value.get("status") if status is None else status
    if (
        not isinstance(expected, str)
        or expected not in STATUSES
        or value.get("status") != expected
        or value.get("operation") != OPERATION
        or value.get("purpose") != PURPOSE
        or type(value.get("season_number")) is not int
        or value.get("season_number") != SEASON_NUMBER
        or not isinstance(value.get("run_status"), str)
        or value.get("run_status") not in RUN_STATUSES
    ):
        raise ValueError("invalid next-adjudication aggregate")
    _run_id(value.get("run_id"))
    _cost(value.get("actual_primary_cost_microusd"), positive=False)
    _cost(value.get("estimated_adjudication_cost_microusd"), positive=False)
    part_count = value.get("adjudication_part_count")
    completed_count = value.get("adjudication_completed_part_count")
    submitted_count = value.get("submitted_part_count")
    if (
        type(part_count) is not int
        or type(completed_count) is not int
        or type(submitted_count) is not int
        or not 0 < completed_count < part_count
        or submitted_count not in (0, 1)
    ):
        raise ValueError("invalid next-adjudication aggregate")
    if expected in {"submitted", "already_submitted"} and submitted_count != 1:
        raise ValueError("invalid next-adjudication aggregate")
    if expected in {"submitted", "already_submitted"} and value["run_status"] != (
        "adjudication_submitted"
    ):
        raise ValueError("invalid next-adjudication aggregate")
    if expected == "reconciliation_required" and submitted_count != 0:
        raise ValueError("invalid next-adjudication aggregate")
    return dict(value)


def parse_aggregate(raw: bytes, *, status: str | None = None) -> dict[str, object]:
    return validate_aggregate(_decode(raw, maximum=OUTPUT_MAX_BYTES), status=status)


def is_reconciliation_error(error: BaseException) -> bool:
    from cinegraph.common.error_messages import (
        SpeakerReviewErrorMessages,
    )

    return str(error) in {
        SpeakerReviewErrorMessages.NEXT_ADJUDICATION_SUBMISSION_RECONCILIATION_REQUIRED,
        SpeakerReviewErrorMessages.BATCH_SUBMISSION_RECONCILIATION_REQUIRED,
    }


__all__ = [
    name
    for name in globals()
    if name.isupper() or name.startswith(("canonical_", "parse_", "validate_", "is_"))
]
