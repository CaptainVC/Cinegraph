from __future__ import annotations

import pytest
from scripts import private_speaker_review_next_final_review_submission_contract as contract


def _request() -> dict[str, object]:
    return {
        "archive_sha256": "a" * 64,
        "authorization_id": "12345678-1234-4234-8234-123456789abc",
        "maximum_authorized_cost_microusd": 5_000_000,
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": "speaker-review-0123456789abcdef",
        "schema_version": contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
    }


def _aggregate(status: str = "submitted", completed: int = 1) -> dict[str, object]:
    return {
        "actual_adjudication_cost_microusd": 10,
        "actual_final_review_cost_microusd": 0,
        "actual_primary_cost_microusd": 10,
        "estimated_final_review_cost_microusd": 10,
        "final_review_completed_part_count": completed,
        "final_review_part_count": 2,
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": _request()["run_id"],
        "run_status": "final_review_submitted",
        "season_number": contract.SEASON_NUMBER,
        "status": status,
        "submitted_part_count": 1 if status in {"submitted", "already_submitted"} else 0,
    }


def test_contract_is_bound_to_part_two() -> None:
    assert contract.PREDECESSOR_COMPLETED_PART_COUNT == 1
    assert contract.SUBMITTED_PART_NUMBER == 2
    assert contract.validate_request(_request()) == _request()


@pytest.mark.parametrize("status", ["submitted", "already_submitted"])
def test_submitted_aggregate_retains_predecessor_count(status: str) -> None:
    assert contract.validate_aggregate(_aggregate(status))
    with pytest.raises(ValueError):
        contract.validate_aggregate(_aggregate(status, completed=2))


def test_one_part_completion_is_provider_free_terminal_result() -> None:
    value = _aggregate("all_parts_completed", completed=2)
    value["final_review_part_count"] = 2
    value["submitted_part_count"] = 0
    assert contract.validate_aggregate(value)["status"] == "all_parts_completed"
