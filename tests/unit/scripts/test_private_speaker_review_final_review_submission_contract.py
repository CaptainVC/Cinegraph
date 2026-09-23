from __future__ import annotations

import pytest
from scripts import private_speaker_review_final_review_submission_contract as contract


def _request() -> dict[str, object]:
    return {
        "archive_sha256": "a" * 64,
        "authorization_id": "123e4567-e89b-42d3-a456-426614174000",
        "maximum_authorized_cost_microusd": 5_000_000,
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": "speaker-review-0123456789abcdef",
        "schema_version": contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
    }


def test_request_is_strict_canonical_and_uuid4_bound() -> None:
    raw = contract.canonical_json(_request())
    assert contract.parse_request(raw) == _request()
    with pytest.raises(ValueError):
        contract.parse_request(raw.replace(b"\n", b"", 1))
    invalid = _request()
    invalid["authorization_id"] = "123e4567-e89b-12d3-a456-426614174000"
    with pytest.raises(ValueError):
        contract.validate_request(invalid)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"archive_sha256":"' + b"a" * 64 + b'","archive_sha256":"' + b"b" * 64 + b'"}\n',
        b'{"value":NaN}\n',
        b'\xef\xbb\xbf{}\n',
        b'{}',
    ],
)
def test_request_wire_rejects_duplicates_nonfinite_bom_and_noncanonical_bytes(
    raw: bytes,
) -> None:
    with pytest.raises(ValueError):
        contract.parse_request(raw)


def test_public_aggregate_contains_no_provider_identifiers() -> None:
    value = {
        "actual_primary_cost_microusd": 100,
        "actual_adjudication_cost_microusd": 200,
        "estimated_final_review_cost_microusd": 300,
        "final_review_completed_part_count": 0,
        "final_review_part_count": 2,
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": "speaker-review-0123456789abcdef",
        "run_status": "final_review_prepared",
        "season_number": contract.SEASON_NUMBER,
        "status": "reconciliation_required",
        "submitted_part_count": 0,
    }
    assert contract.validate_aggregate(value, status="reconciliation_required") == value
    assert not any("batch" in key or "file" in key or "path" in key for key in value)
