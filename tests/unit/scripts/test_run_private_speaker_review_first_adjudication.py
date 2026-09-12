"""Focused Phase 69 coordinator boundary checks."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts import run_private_speaker_review_first_adjudication as root

ARCHIVE = "a" * 64
RUN_ID = "speaker-review-0123456789abcdef"
AUTHORIZATION_ID = "123e4567-e89b-42d3-a456-426614174000"
REQUEST = {
    "archive_sha256": ARCHIVE,
    "authorization_id": AUTHORIZATION_ID,
    "maximum_authorized_cost_microusd": 5_000_000,
    "operation": root.contract.OPERATION,
    "purpose": root.contract.PURPOSE,
    "run_id": RUN_ID,
    "schema_version": root.contract.PROTOCOL_VERSION,
    "season_number": root.contract.SEASON_NUMBER,
}


def _aggregate(status: str = "submitted") -> dict[str, object]:
    return root.contract.validate_aggregate(
        {
            "actual_primary_cost_microusd": 1,
            "adjudication_part_count": 1,
            "estimated_adjudication_cost_microusd": 1,
            "operation": root.contract.OPERATION,
            "purpose": root.contract.PURPOSE,
            "run_id": RUN_ID,
            "run_status": "adjudication_submitted"
            if status != "reconciliation_required"
            else "adjudication_prepared",
            "season_number": 2,
            "status": status,
            "submitted_part_count": 1 if status != "reconciliation_required" else 0,
        },
        status=status,
    )


def _bindings() -> dict[str, str]:
    return {
        root.contract.ENV_EXPECTED_REQUEST_SHA256: "1" * 64,
        root.contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: "2" * 64,
        root.contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: "3" * 64,
        root.contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: "4" * 64,
        root.contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256: "5" * 64,
        root.contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256: "6" * 64,
    }


def test_worker_argv_explicitly_binds_request_and_all_five_digests(tmp_path: Path) -> None:
    args = root._worker_args(REQUEST, tmp_path / "review-runs" / RUN_ID, _bindings())
    assert args.count("--env") == 10
    for name, value in {**root._env(REQUEST, _bindings())}.items():
        assert f"{name}={value}" in args
    assert (
        f"{tmp_path / 'review-runs'}:{root.host.REVIEW_FIRST_ADJUDICATION_RUNS_TARGET}:rw" in args
    )


def test_result_cap_rejects_actual_plus_estimate_over_authorization() -> None:
    value = _aggregate() | {"actual_primary_cost_microusd": 5_000_000}
    with pytest.raises(root.FirstAdjudicationSubmissionError):
        root._validate_result(value, status="submitted", maximum=5_000_000)


def test_result_validation_accepts_submitted_and_replay_statuses() -> None:
    assert (
        root._validate_result(_aggregate(), status="submitted", maximum=5_000_000)["status"]
        == "submitted"
    )
    replay = _aggregate("already_submitted")
    assert (
        root._validate_result(replay, status="already_submitted", maximum=5_000_000)["status"]
        == "already_submitted"
    )


def test_root_intent_schema_is_closed() -> None:
    assert "request_sha256" in root._ROOT_INTENT_KEYS
    assert "pre_derived_set_sha256" in root._ROOT_INTENT_KEYS
    assert root._ROOT_RECEIPT_KEYS == root._ROOT_INTENT_KEYS | {
        "post_state_sha256",
        "post_journal_set_sha256",
        "result",
    }
