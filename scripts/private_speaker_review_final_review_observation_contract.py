"""Strict wire contract for observing final-review part one."""

from __future__ import annotations

import json
import re
import uuid
from typing import Final, Mapping

COMMAND: Final = "speaker-review-observe-final-review-part-one-v1"
PROTOCOL_VERSION: Final = 1
PURPOSE: Final = "speaker_review"
SEASON_NUMBER: Final = 2
OPERATION: Final = "observe_final_review_part_one"
MAXIMUM_AUTHORIZED_COST_MICROUSD: Final = 5_000_000
SUBMITTED_PART_COUNT: Final = 1
OBSERVED_PART_NUMBER: Final = 1
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
        "actual_adjudication_cost_microusd",
        "actual_final_review_cost_microusd",
        "actual_primary_cost_microusd",
        "final_review_completed_part_count",
        "estimated_final_review_cost_microusd",
        "final_review_part_count",
        "maximum_authorized_cost_microusd",
        "state_maximum_cost_microusd",
        "operation",
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
RUN_STATUSES: Final = frozenset({"final_review_submitted", "failed"})

ENV_ARCHIVE_SHA256: Final = "CINEGRAPH_SPEAKER_REVIEW_ARCHIVE_SHA256"
ENV_RUN_ID: Final = "CINEGRAPH_SPEAKER_REVIEW_RUN_ID"
ENV_AUTHORIZATION_ID: Final = "CINEGRAPH_SPEAKER_REVIEW_AUTHORIZATION_ID"
ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: Final = (
    "CINEGRAPH_SPEAKER_REVIEW_MAXIMUM_AUTHORIZED_COST_MICROUSD"
)
ENV_EXPECTED_SUBMISSION_RECEIPT_SHA256: Final = (
    "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_SUBMISSION_RECEIPT_SHA256"
)
ENV_EXPECTED_ESTIMATED_FINAL_REVIEW_COST_MICROUSD: Final = (
    "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_ESTIMATED_FINAL_REVIEW_COST_MICROUSD"
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


def _decode(raw: bytes, maximum: int) -> dict[str, object]:
    if (
        not isinstance(raw, bytes)
        or not raw
        or len(raw) > maximum
        or not raw.endswith(b"\n")
        or raw.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"))
    ):
        raise ValueError("invalid final-review observation wire value")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("invalid final-review observation wire value") from error
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise ValueError("invalid final-review observation wire value")
    return value


def validate_request(value: Mapping[str, object]) -> dict[str, object]:
    decoded = _decode(canonical_json(value), REQUEST_MAX_BYTES)
    if (
        set(decoded) != REQUEST_KEYS
        or type(decoded.get("schema_version")) is not int
        or decoded["schema_version"] != PROTOCOL_VERSION
        or decoded.get("operation") != OPERATION
        or decoded.get("purpose") != PURPOSE
        or type(decoded.get("season_number")) is not int
        or decoded["season_number"] != SEASON_NUMBER
    ):
        raise ValueError("invalid final-review observation request")
    digest, run_id, auth_id = (
        decoded.get("archive_sha256"),
        decoded.get("run_id"),
        decoded.get("authorization_id"),
    )
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        raise ValueError("invalid archive digest")
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise ValueError("invalid run id")
    if not isinstance(auth_id, str) or _UUID4.fullmatch(auth_id) is None:
        raise ValueError("invalid authorization id")
    parsed_auth_id = uuid.UUID(auth_id)
    if parsed_auth_id.version != 4 or str(parsed_auth_id) != auth_id:
        raise ValueError("invalid authorization id")
    cost = decoded.get("maximum_authorized_cost_microusd")
    if type(cost) is not int or cost <= 0 or cost > MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise ValueError("invalid cost")
    return decoded


def parse_request(raw: bytes) -> dict[str, object]:
    return validate_request(_decode(raw, REQUEST_MAX_BYTES))


def validate_aggregate(value: object, *, status: str | None = None) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != AGGREGATE_KEYS:
        raise ValueError("invalid final-review observation aggregate")
    expected = value.get("status") if status is None else status
    if (
        expected not in STATUSES
        or value.get("status") != expected
        or value.get("operation") != OPERATION
        or value.get("purpose") != PURPOSE
        or type(value.get("season_number")) is not int
        or value["season_number"] != SEASON_NUMBER
        or value.get("run_status") not in RUN_STATUSES
    ):
        raise ValueError("invalid final-review observation aggregate")
    run_id = value.get("run_id")
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise ValueError("invalid run id")
    for field in (
        "actual_primary_cost_microusd",
        "actual_adjudication_cost_microusd",
        "actual_final_review_cost_microusd",
        "estimated_final_review_cost_microusd",
        "maximum_authorized_cost_microusd",
        "state_maximum_cost_microusd",
    ):
        item = value.get(field)
        if type(item) is not int or item < 0 or item > MAXIMUM_AUTHORIZED_COST_MICROUSD:
            raise ValueError("invalid cost")
    if value["actual_final_review_cost_microusd"] != 0:
        raise ValueError("unexpected observed cost")
    if value["actual_primary_cost_microusd"] + value["actual_adjudication_cost_microusd"] + value[
        "estimated_final_review_cost_microusd"
    ] > min(value["maximum_authorized_cost_microusd"], value["state_maximum_cost_microusd"]):
        raise ValueError("authorized cost exceeded")
    count, completed = (
        value.get("final_review_part_count"),
        value.get("final_review_completed_part_count"),
    )
    if type(count) is not int or type(completed) is not int or count < 1 or completed not in (0, 1):
        raise ValueError("invalid final-review observation count")
    if expected in {"waiting", "reconciliation_required"} and (
        completed != 0 or value["run_status"] != "final_review_submitted"
    ):
        raise ValueError("invalid final-review waiting aggregate")
    if expected in {"observed", "already_observed"} and (
        completed != 1 or value["run_status"] != "final_review_submitted"
    ):
        raise ValueError("invalid final-review observed aggregate")
    if expected == "failed" and (completed != 0 or value["run_status"] != "failed"):
        raise ValueError("invalid final-review failed aggregate")
    return dict(value)


def parse_aggregate(raw: bytes, *, status: str | None = None) -> dict[str, object]:
    return validate_aggregate(_decode(raw, OUTPUT_MAX_BYTES), status=status)


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
    }
]
