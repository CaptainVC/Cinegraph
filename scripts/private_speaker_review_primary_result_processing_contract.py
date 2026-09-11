"""Strict contract for provider-free primary-result processing.

This boundary consumes a fully observed primary run and performs only local
result parsing/decision preparation.  It deliberately carries no provider
identifiers, result payloads, filesystem paths, or credentials.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Final, Mapping

COMMAND: Final = "speaker-review-process-primary-results-v1"
PROTOCOL_VERSION: Final = 1
PURPOSE: Final = "speaker_review"
SEASON_NUMBER: Final = 2
OPERATION: Final = "process_primary_results"
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
        "accepted_by_consensus",
        "adjudication_part_count",
        "candidate_count",
        "operation",
        "primary_completed_part_count",
        "primary_part_count",
        "purpose",
        "run_id",
        "run_status",
        "season_number",
        "status",
        "needs_human",
    }
)
STATUSES: Final = frozenset({"adjudication_prepared", "completed", "already_processed"})
RUN_STATUSES: Final = frozenset({"adjudication_prepared", "completed"})
MAXIMUM_AUTHORIZED_COST_MICROUSD: Final = 5_000_000

ENV_ARCHIVE_SHA256: Final = "CINEGRAPH_SPEAKER_REVIEW_ARCHIVE_SHA256"
ENV_RUN_ID: Final = "CINEGRAPH_SPEAKER_REVIEW_RUN_ID"
ENV_AUTHORIZATION_ID: Final = "CINEGRAPH_SPEAKER_REVIEW_AUTHORIZATION_ID"
ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: Final = (
    "CINEGRAPH_SPEAKER_REVIEW_MAXIMUM_AUTHORIZED_COST_MICROUSD"
)
ENV_EXPECTED_PRE_RUN_STATE_SHA256: Final = (
    "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_PRE_RUN_STATE_SHA256"
)
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

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^speaker-review-[0-9a-f]{16}$")
_UUID4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def canonical_json(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
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
        not isinstance(raw, bytes)
        or not raw
        or len(raw) > maximum
        or not raw.endswith(b"\n")
        or raw.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"))
    ):
        raise ValueError("invalid primary-result-processing wire value")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("invalid primary-result-processing wire value") from error
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise ValueError("invalid primary-result-processing wire value")
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


def _cost(value: object) -> None:
    if type(value) is not int or value <= 0 or value > MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise ValueError("invalid authorized cost")


def _digest(value: object) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError("invalid digest")


def validate_request(value: Mapping[str, object]) -> dict[str, object]:
    raw = canonical_json(value)
    decoded = _decode_canonical(raw, maximum=REQUEST_MAX_BYTES)
    if set(decoded) != REQUEST_KEYS:
        raise ValueError("invalid primary-result-processing request")
    if (
        decoded.get("schema_version") != PROTOCOL_VERSION
        or decoded.get("operation") != OPERATION
        or decoded.get("purpose") != PURPOSE
        or decoded.get("season_number") != SEASON_NUMBER
        or not isinstance(decoded.get("archive_sha256"), str)
        or _SHA256.fullmatch(decoded["archive_sha256"]) is None
    ):
        raise ValueError("invalid primary-result-processing request")
    _run_id(decoded.get("run_id"))
    _authorization_id(decoded.get("authorization_id"))
    _cost(decoded.get("maximum_authorized_cost_microusd"))
    return decoded


def parse_request(raw: bytes) -> dict[str, object]:
    return validate_request(_decode_canonical(raw, maximum=REQUEST_MAX_BYTES))


def validate_aggregate(
    value: object, *, status: str | None = None
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != AGGREGATE_KEYS:
        raise ValueError("invalid primary-result-processing aggregate")
    expected = value.get("status") if status is None else status
    if expected not in STATUSES or value.get("status") != expected:
        raise ValueError("invalid primary-result-processing aggregate")
    if (
        value.get("operation") != OPERATION
        or value.get("purpose") != PURPOSE
        or value.get("season_number") != SEASON_NUMBER
        or value.get("run_status") not in RUN_STATUSES
    ):
        raise ValueError("invalid primary-result-processing aggregate")
    _run_id(value.get("run_id"))
    for key in (
        "accepted_by_consensus",
        "adjudication_part_count",
        "candidate_count",
        "primary_completed_part_count",
        "primary_part_count",
        "needs_human",
    ):
        if type(value.get(key)) is not int or value[key] < 0:
            raise ValueError("invalid primary-result-processing aggregate")
    if (
        value["candidate_count"] <= 0
        or value["primary_part_count"] <= 0
        or value["primary_completed_part_count"] != value["primary_part_count"]
        or value["accepted_by_consensus"] > value["candidate_count"]
        or value["needs_human"] > value["candidate_count"]
    ):
        raise ValueError("invalid primary-result-processing aggregate")
    if expected != "already_processed" and value["run_status"] != expected:
        raise ValueError("invalid primary-result-processing aggregate")
    if value["run_status"] == "adjudication_prepared":
        if (
            value["adjudication_part_count"] <= 0
            or value["accepted_by_consensus"] >= value["candidate_count"]
            or value["needs_human"] != 0
        ):
            raise ValueError("invalid primary-result-processing aggregate")
    elif value["run_status"] == "completed" and (
        value["adjudication_part_count"] != 0
        or value["accepted_by_consensus"] != value["candidate_count"]
        or value["needs_human"] != 0
    ):
        raise ValueError("invalid primary-result-processing aggregate")
    return dict(value)


def parse_aggregate(raw: bytes, *, status: str | None = None) -> dict[str, object]:
    return validate_aggregate(
        _decode_canonical(raw, maximum=OUTPUT_MAX_BYTES), status=status
    )


__all__ = [
    "AGGREGATE_KEYS",
    "COMMAND",
    "ENV_ARCHIVE_SHA256",
    "ENV_AUTHORIZATION_ID",
    "ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256",
    "ENV_EXPECTED_PRE_DERIVED_SET_SHA256",
    "ENV_EXPECTED_PRE_JOURNAL_SET_SHA256",
    "ENV_EXPECTED_PRE_OUTPUT_SET_SHA256",
    "ENV_EXPECTED_PRE_RUN_STATE_SHA256",
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
    "parse_aggregate",
    "parse_request",
    "validate_aggregate",
    "validate_request",
]
