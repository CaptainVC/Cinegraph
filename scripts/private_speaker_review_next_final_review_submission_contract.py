"""Strict wire contract for submitting final-review part two.

This boundary is deliberately separate from the initial final-review submit:
the request is authorized only after part one has been observed successfully.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Final, Mapping

COMMAND: Final = "speaker-review-submit-next-final-review-v1"
PROTOCOL_VERSION: Final = 1
PURPOSE: Final = "speaker_review"
SEASON_NUMBER: Final = 2
OPERATION: Final = "submit_next_final_review"
REQUEST_MAX_BYTES: Final = 1_024
OUTPUT_MAX_BYTES: Final = 4_096
MAXIMUM_AUTHORIZED_COST_MICROUSD: Final = 5_000_000
PREDECESSOR_COMPLETED_PART_COUNT: Final = 1
SUBMITTED_PART_NUMBER: Final = 2

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
        "actual_adjudication_cost_microusd",
        "actual_final_review_cost_microusd",
        "actual_primary_cost_microusd",
        "estimated_final_review_cost_microusd",
        "final_review_completed_part_count",
        "final_review_part_count",
        "operation",
        "purpose",
        "run_id",
        "run_status",
        "season_number",
        "status",
        "submitted_part_count",
    }
)
STATUSES: Final = frozenset({"submitted", "already_submitted", "all_parts_completed", "reconciliation_required"})
RUN_STATUSES: Final = frozenset({"final_review_submitted"})

ENV_ARCHIVE_SHA256: Final = "CINEGRAPH_SPEAKER_REVIEW_ARCHIVE_SHA256"
ENV_RUN_ID: Final = "CINEGRAPH_SPEAKER_REVIEW_RUN_ID"
ENV_AUTHORIZATION_ID: Final = "CINEGRAPH_SPEAKER_REVIEW_AUTHORIZATION_ID"
ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: Final = "CINEGRAPH_SPEAKER_REVIEW_MAXIMUM_AUTHORIZED_COST_MICROUSD"
ENV_EXPECTED_PRE_RUN_STATE_SHA256: Final = "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_PRE_RUN_STATE_SHA256"
ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: Final = "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_PRE_ARTIFACT_SET_SHA256"
ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: Final = "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_PRE_JOURNAL_SET_SHA256"
ENV_EXPECTED_PRE_OUTPUT_SET_SHA256: Final = "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_PRE_OUTPUT_SET_SHA256"
ENV_EXPECTED_PRE_DERIVED_SET_SHA256: Final = "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_PRE_DERIVED_SET_SHA256"
ENV_EXPECTED_REQUEST_SHA256: Final = "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_REQUEST_SHA256"
ENV_EXPECTED_PHASE80_OBSERVATION_RECEIPT_SHA256: Final = "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_PHASE80_OBSERVATION_RECEIPT_SHA256"
ENV_EXPECTED_PHASE79_SUBMISSION_RECEIPT_SHA256: Final = "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_PHASE79_SUBMISSION_RECEIPT_SHA256"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^speaker-review-[0-9a-f]{16}$")
_UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def canonical_json(value: Mapping[str, object]) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _decode(raw: bytes, *, maximum: int) -> dict[str, object]:
    if not isinstance(raw, bytes) or not raw or len(raw) > maximum or not raw.endswith(b"\n"):
        raise ValueError("invalid next-final-review wire value")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("invalid next-final-review wire value") from error
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise ValueError("invalid next-final-review wire value")
    return value


def _digest(value: object) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError("invalid digest")


def _run(value: object) -> None:
    if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
        raise ValueError("invalid run id")


def _auth(value: object) -> None:
    if not isinstance(value, str) or _UUID4.fullmatch(value) is None:
        raise ValueError("invalid authorization id")
    parsed = uuid.UUID(value)
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("invalid authorization id")


def validate_request(value: Mapping[str, object]) -> dict[str, object]:
    decoded = _decode(canonical_json(value), maximum=REQUEST_MAX_BYTES)
    if set(decoded) != REQUEST_KEYS or type(decoded.get("schema_version")) is not int or decoded.get("schema_version") != PROTOCOL_VERSION or decoded.get("operation") != OPERATION or decoded.get("purpose") != PURPOSE or type(decoded.get("season_number")) is not int or decoded.get("season_number") != SEASON_NUMBER:
        raise ValueError("invalid next-final-review request")
    _digest(decoded.get("archive_sha256"))
    _run(decoded.get("run_id"))
    _auth(decoded.get("authorization_id"))
    cost = decoded.get("maximum_authorized_cost_microusd")
    if type(cost) is not int or cost <= 0 or cost > MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise ValueError("invalid authorized cost")
    return decoded


def parse_request(raw: bytes) -> dict[str, object]:
    return validate_request(_decode(raw, maximum=REQUEST_MAX_BYTES))


def validate_aggregate(value: object, *, status: str | None = None) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != AGGREGATE_KEYS:
        raise ValueError("invalid next-final-review aggregate")
    expected = value.get("status") if status is None else status
    if expected not in STATUSES or value.get("status") != expected or value.get("operation") != OPERATION or value.get("purpose") != PURPOSE or value.get("season_number") != SEASON_NUMBER or value.get("run_status") not in RUN_STATUSES:
        raise ValueError("invalid next-final-review aggregate")
    _run(value.get("run_id"))
    for key in ("actual_primary_cost_microusd", "actual_adjudication_cost_microusd", "actual_final_review_cost_microusd", "estimated_final_review_cost_microusd"):
        item = value.get(key)
        if type(item) is not int or item < 0 or item > MAXIMUM_AUTHORIZED_COST_MICROUSD:
            raise ValueError("invalid cost")
    count, completed, submitted = (value.get("final_review_part_count"), value.get("final_review_completed_part_count"), value.get("submitted_part_count"))
    if type(count) is not int or type(completed) is not int or type(submitted) is not int or count <= 0 or completed < PREDECESSOR_COMPLETED_PART_COUNT or completed > count or submitted not in (0, 1):
        raise ValueError("invalid final-review counts")
    if expected in {"submitted", "already_submitted"} and (submitted != 1 or completed != PREDECESSOR_COMPLETED_PART_COUNT):
        raise ValueError("invalid submitted aggregate")
    if expected == "all_parts_completed" and (submitted != 0 or completed != count):
        raise ValueError("invalid completed aggregate")
    if expected == "reconciliation_required" and (submitted != 0 or completed != PREDECESSOR_COMPLETED_PART_COUNT):
        raise ValueError("invalid reconciliation aggregate")
    return dict(value)


def parse_aggregate(raw: bytes, *, status: str | None = None) -> dict[str, object]:
    return validate_aggregate(_decode(raw, maximum=OUTPUT_MAX_BYTES), status=status)


def is_reconciliation_error(error: BaseException) -> bool:
    from cinegraph.common.error_messages import SpeakerReviewErrorMessages

    return str(error) in {
        SpeakerReviewErrorMessages.BATCH_SUBMISSION_RECONCILIATION_REQUIRED,
        SpeakerReviewErrorMessages.NEXT_FINAL_REVIEW_SUBMISSION_RECONCILIATION_REQUIRED,
    }


__all__ = [name for name in globals() if name.isupper() or name in {"canonical_json", "parse_request", "parse_aggregate", "validate_request", "validate_aggregate", "is_reconciliation_error"}]
