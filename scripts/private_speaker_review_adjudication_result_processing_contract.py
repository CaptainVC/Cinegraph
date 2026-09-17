"""Strict secretless wire contract for Phase 77 adjudication processing."""

from __future__ import annotations

import json
import re
import uuid
from typing import Final, Mapping

COMMAND: Final = "speaker-review-process-adjudication-results-v1"
PROTOCOL_VERSION: Final = 1
PURPOSE: Final = "speaker_review"
SEASON_NUMBER: Final = 2
OPERATION: Final = "process_adjudication_results"
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
        "accepted_by_consensus",
        "accepted_by_adjudication",
        "actual_adjudication_cost_microusd",
        "actual_primary_cost_microusd",
        "adjudication_completed_part_count",
        "adjudication_part_count",
        "candidate_count",
        "final_review_part_count",
        "maximum_authorized_cost_microusd",
        "needs_human",
        "operation",
        "primary_completed_part_count",
        "primary_part_count",
        "purpose",
        "run_status",
        "season_number",
        "status",
    }
)
STATUSES: Final = frozenset({"final_review_prepared", "completed", "already_processed"})
RUN_STATUSES: Final = frozenset({"final_review_prepared", "completed"})
ENV_ARCHIVE_SHA256: Final = "CINEGRAPH_SPEAKER_REVIEW_ARCHIVE_SHA256"
ENV_RUN_ID: Final = "CINEGRAPH_SPEAKER_REVIEW_RUN_ID"
ENV_AUTHORIZATION_ID: Final = "CINEGRAPH_SPEAKER_REVIEW_AUTHORIZATION_ID"
ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: Final = (
    "CINEGRAPH_SPEAKER_REVIEW_MAXIMUM_AUTHORIZED_COST_MICROUSD"
)
ENV_EXPECTED_STATE_DIGEST: Final = "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_STATE_SHA256"
ENV_EXPECTED_ARTIFACTS_DIGEST: Final = "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_ARTIFACTS_SHA256"
ENV_EXPECTED_REQUESTS_DIGEST: Final = "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_REQUESTS_SHA256"
ENV_EXPECTED_JOURNALS_DIGEST: Final = "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_JOURNALS_SHA256"
ENV_EXPECTED_OUTPUTS_DIGEST: Final = "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_OUTPUTS_SHA256"
ENV_EXPECTED_DERIVED_DIGEST: Final = "CINEGRAPH_SPEAKER_REVIEW_EXPECTED_DERIVED_SHA256"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^speaker-review-[0-9a-f]{16}$")
_UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def canonical_json(value: Mapping[str, object]) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _decode(raw: bytes, maximum: int) -> dict[str, object]:
    if not isinstance(raw, bytes) or not raw or len(raw) > maximum or not raw.endswith(b"\n") or raw.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        raise ValueError("invalid adjudication-processing wire value")
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value
    try:
        decoded = raw.decode("utf-8")
    except UnicodeError as error:
        raise ValueError("invalid adjudication-processing wire value") from error
    value = json.loads(
        decoded,
        object_pairs_hook=reject_duplicates,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")),
    )
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise ValueError("invalid adjudication-processing wire value")
    return value


def _run_id(value: object) -> None:
    if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
        raise ValueError("invalid run id")


def _digest(value: object) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError("invalid digest")


def validate_request(value: Mapping[str, object]) -> dict[str, object]:
    decoded = _decode(canonical_json(value), REQUEST_MAX_BYTES)
    if set(decoded) != REQUEST_KEYS or decoded.get("schema_version") != PROTOCOL_VERSION or decoded.get("operation") != OPERATION or decoded.get("purpose") != PURPOSE or decoded.get("season_number") != SEASON_NUMBER:
        raise ValueError("invalid adjudication-processing request")
    _digest(decoded.get("archive_sha256"))
    _run_id(decoded.get("run_id"))
    authorization = decoded.get("authorization_id")
    if not isinstance(authorization, str) or _UUID4.fullmatch(authorization) is None or uuid.UUID(authorization).version != 4:
        raise ValueError("invalid authorization id")
    cost = decoded.get("maximum_authorized_cost_microusd")
    if type(cost) is not int or cost <= 0 or cost > MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise ValueError("invalid authorized cost")
    return decoded


def parse_request(raw: bytes) -> dict[str, object]:
    return validate_request(_decode(raw, REQUEST_MAX_BYTES))


def validate_aggregate(value: object, *, status: str | None = None) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != AGGREGATE_KEYS:
        raise ValueError("invalid adjudication-processing aggregate")
    expected = value.get("status") if status is None else status
    if expected not in STATUSES or value.get("status") != expected:
        raise ValueError("invalid adjudication-processing aggregate")
    if value.get("operation") != OPERATION or value.get("purpose") != PURPOSE or value.get("season_number") != SEASON_NUMBER or value.get("run_status") not in RUN_STATUSES:
        raise ValueError("invalid adjudication-processing aggregate")
    for key in AGGREGATE_KEYS - {"operation", "purpose", "season_number", "run_status", "status"}:
        if type(value.get(key)) is not int or value[key] < 0:
            raise ValueError("invalid adjudication-processing aggregate")
    if (
        value["candidate_count"] <= 0
        or value["primary_part_count"] <= 0
        or value["primary_completed_part_count"] != value["primary_part_count"]
        or value["adjudication_part_count"] <= 0
        or value["adjudication_completed_part_count"]
        != value["adjudication_part_count"]
        or value["accepted_by_consensus"]
        + value["accepted_by_adjudication"]
        + value["needs_human"]
        != value["candidate_count"]
        or value["actual_adjudication_cost_microusd"]
        + value["actual_primary_cost_microusd"]
        > value["maximum_authorized_cost_microusd"]
        or value["maximum_authorized_cost_microusd"] <= 0
        or value["maximum_authorized_cost_microusd"]
        > MAXIMUM_AUTHORIZED_COST_MICROUSD
    ):
        raise ValueError("invalid adjudication-processing aggregate")
    if expected != "already_processed" and value["run_status"] != expected:
        raise ValueError("invalid adjudication-processing aggregate")
    if value["run_status"] == "final_review_prepared" and (
        value["final_review_part_count"] <= 0
        or value["final_review_part_count"] > value["needs_human"]
        or value["needs_human"] <= 0
    ):
        raise ValueError("invalid adjudication-processing aggregate")
    if value["run_status"] == "completed" and (value["final_review_part_count"] != 0 or value["needs_human"] != 0):
        raise ValueError("invalid adjudication-processing aggregate")
    return dict(value)


def parse_aggregate(raw: bytes, *, status: str | None = None) -> dict[str, object]:
    return validate_aggregate(_decode(raw, OUTPUT_MAX_BYTES), status=status)
