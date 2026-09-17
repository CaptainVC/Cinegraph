from __future__ import annotations

import json

import pytest
from scripts import private_speaker_review_adjudication_result_processing_contract as contract

RUN_ID = "speaker-review-0123456789abcdef"
AUTHORIZATION_ID = "123e4567-e89b-42d3-a456-426614174000"


def _request() -> dict[str, object]:
    return {
        "archive_sha256": "a" * 64,
        "authorization_id": AUTHORIZATION_ID,
        "maximum_authorized_cost_microusd": 5_000_000,
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "schema_version": contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
    }


def _aggregate(*, status: str = "final_review_prepared", run_status: str | None = None) -> dict[str, object]:
    resolved = run_status or status
    return {
        "accepted_by_consensus": 1,
        "accepted_by_adjudication": 0,
        "actual_adjudication_cost_microusd": 2,
        "actual_primary_cost_microusd": 3,
        "adjudication_completed_part_count": 1,
        "adjudication_part_count": 1,
        "candidate_count": 2,
        "final_review_part_count": 1 if resolved == "final_review_prepared" else 0,
        "maximum_authorized_cost_microusd": 5_000_000,
        "needs_human": 1 if resolved == "final_review_prepared" else 0,
        "operation": contract.OPERATION,
        "primary_completed_part_count": 1,
        "primary_part_count": 1,
        "purpose": contract.PURPOSE,
        "run_status": resolved,
        "season_number": contract.SEASON_NUMBER,
        "status": status,
    }


def test_request_and_aggregate_are_exact_canonical_wire_values() -> None:
    request = _request()
    encoded = contract.canonical_json(request)
    assert encoded == json.dumps(request, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    assert contract.parse_request(encoded) == request
    assert contract.parse_aggregate(contract.canonical_json(_aggregate())) == _aggregate()


@pytest.mark.parametrize(
    "raw",
    [
        b'\xef\xbb\xbf' + contract.canonical_json(_request()),
        b"\xff\xfe\n",
        contract.canonical_json(_request()).replace(b'"run_id":', b'"run_id":"duplicate", "run_id":', 1),
        contract.canonical_json(_request()).replace(b"\n", b" \n"),
    ],
)
def test_request_rejects_bom_duplicate_keys_and_noncanonical_bytes(raw: bytes) -> None:
    with pytest.raises(ValueError):
        contract.parse_request(raw)


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "speaker-review-not-a-run"),
        ("authorization_id", "123e4567-e89b-12d3-a456-426614174001"),
        ("maximum_authorized_cost_microusd", 0),
        ("maximum_authorized_cost_microusd", 5_000_001),
        ("maximum_authorized_cost_microusd", True),
        ("archive_sha256", "z" * 64),
    ],
)
def test_request_rejects_uuid_digest_and_cost_contract_violations(field: str, value: object) -> None:
    request = _request()
    request[field] = value
    with pytest.raises(ValueError):
        contract.validate_request(request)


@pytest.mark.parametrize(
    "status,run_status",
    [
        ("final_review_prepared", "completed"),
        ("completed", "final_review_prepared"),
        ("unknown", "completed"),
    ],
)
def test_aggregate_status_and_count_invariants_are_strict(status: str, run_status: str) -> None:
    with pytest.raises(ValueError):
        contract.validate_aggregate(_aggregate(status=status, run_status=run_status), status=status)


@pytest.mark.parametrize(
    "field",
    [
        "candidate_count",
        "primary_part_count",
        "primary_completed_part_count",
        "adjudication_part_count",
        "adjudication_completed_part_count",
        "actual_primary_cost_microusd",
        "actual_adjudication_cost_microusd",
    ],
)
def test_aggregate_rejects_negative_and_over_cap_values(field: str) -> None:
    value = _aggregate()
    value[field] = -1
    with pytest.raises(ValueError):
        contract.validate_aggregate(value)
    if field.endswith("cost_microusd"):
        value = _aggregate()
        value[field] = contract.MAXIMUM_AUTHORIZED_COST_MICROUSD + 1
        with pytest.raises(ValueError):
            contract.validate_aggregate(value)


def test_aggregate_rejects_wrong_partition_totals_and_final_review_counts() -> None:
    value = _aggregate()
    value["candidate_count"] = 3
    with pytest.raises(ValueError):
        contract.validate_aggregate(value)


def test_aggregate_rejects_combined_cost_above_authorized_cap() -> None:
    value = _aggregate()
    value["actual_primary_cost_microusd"] = 4_000_000
    value["actual_adjudication_cost_microusd"] = 2_000_001
    with pytest.raises(ValueError):
        contract.validate_aggregate(value)
    value = _aggregate()
    value["final_review_part_count"] = 0
    with pytest.raises(ValueError):
        contract.validate_aggregate(value)
    value = _aggregate()
    value["needs_human"] = 0
    with pytest.raises(ValueError):
        contract.validate_aggregate(value)
