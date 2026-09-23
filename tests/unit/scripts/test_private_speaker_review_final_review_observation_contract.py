from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from scripts import private_speaker_review_final_review_observation_contract as contract
from scripts import run_private_speaker_review_final_review_observation as boundary


def _request() -> dict[str, object]:
    return {
        "archive_sha256": "a" * 64,
        "authorization_id": str(uuid.uuid4()),
        "maximum_authorized_cost_microusd": 5_000_000,
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": "speaker-review-0123456789abcdef",
        "schema_version": contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
    }


def _aggregate(status: str, *, completed: int, run_status: str) -> dict[str, object]:
    return {
        "actual_adjudication_cost_microusd": 50,
        "actual_final_review_cost_microusd": 0,
        "actual_primary_cost_microusd": 100,
        "estimated_final_review_cost_microusd": 200,
        "final_review_completed_part_count": completed,
        "final_review_part_count": 3,
        "maximum_authorized_cost_microusd": 5_000_000,
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": "speaker-review-0123456789abcdef",
        "run_status": run_status,
        "season_number": contract.SEASON_NUMBER,
        "state_maximum_cost_microusd": 2_000_000,
        "status": status,
    }


def test_request_is_canonical_and_exact() -> None:
    value = _request()
    encoded = contract.canonical_json(value)
    assert contract.parse_request(encoded) == value
    assert contract.COMMAND == "speaker-review-observe-final-review-part-one-v1"


@pytest.mark.parametrize("field,value", [("operation", "observe_all"), ("season_number", True)])
def test_request_rejects_scope_widening(field: str, value: object) -> None:
    request = _request()
    request[field] = value
    with pytest.raises(ValueError):
        contract.validate_request(request)


def test_authorization_id_must_be_canonical_uuid4() -> None:
    request = _request()
    request["authorization_id"] = str(uuid.uuid1())
    with pytest.raises(ValueError):
        contract.validate_request(request)


def test_observed_means_durable_part_one_and_submitted_checkpoint() -> None:
    value = _aggregate("observed", completed=1, run_status="final_review_submitted")
    assert contract.validate_aggregate(value)["final_review_completed_part_count"] == 1


@pytest.mark.parametrize(
    "value",
    [
        _aggregate("observed", completed=0, run_status="final_review_submitted"),
        _aggregate("waiting", completed=1, run_status="final_review_submitted"),
        _aggregate("failed", completed=0, run_status="final_review_submitted"),
    ],
)
def test_aggregate_enforces_status_and_count_pairing(value: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        contract.validate_aggregate(value)


def test_observation_cannot_claim_provider_cost_or_exceed_state_ceiling() -> None:
    value = _aggregate("observed", completed=1, run_status="final_review_submitted")
    value["actual_final_review_cost_microusd"] = 1
    with pytest.raises(ValueError):
        contract.validate_aggregate(value)
    value["actual_final_review_cost_microusd"] = 0
    value["estimated_final_review_cost_microusd"] = 2_000_000
    with pytest.raises(ValueError):
        contract.validate_aggregate(value)


def test_inventory_transition_rejects_existing_file_mutation() -> None:
    before = {
        "run-state.json": b"before-state",
        "final-review-part-0001-requests.jsonl": b"request",
        ".final-review-part-0001-submission-completed.json": b"journal",
    }
    after = {**before, "final-review-part-0001-output.jsonl": b"output"}
    after[".final-review-part-0001-submission-completed.json"] = b"tampered"

    with pytest.raises(boundary.FinalReviewObservationError):
        boundary._validate_inventory_transition(
            before,
            after,
            allowed_additions={"final-review-part-0001-output.jsonl"},
        )


def test_inventory_transition_rejects_file_removal() -> None:
    before = {
        "run-state.json": b"before-state",
        "final-review-part-0001-requests.jsonl": b"request",
    }
    after = {"run-state.json": b"after-state"}

    with pytest.raises(boundary.FinalReviewObservationError):
        boundary._validate_inventory_transition(before, after, allowed_additions=set())


def test_waiting_intent_can_resume_only_when_inventory_is_unchanged(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _request()
    run = tmp_path / "run"
    run.mkdir()
    baseline = {"run-state.json": b"submitted-state", "request.jsonl": b"part-one"}
    receipt_root = tmp_path / "submission-receipts"
    receipt_root.mkdir()
    observation_root = tmp_path / "observation-receipts"
    observation_root.mkdir()
    submission_intent = {"run_id": request["run_id"]}
    submission_receipt = {"intent_sha256": "p79-intent"}
    intent_sha = boundary._sha(boundary._canonical(submission_intent))
    receipt_sha = boundary._sha(boundary._canonical(submission_receipt))
    (receipt_root / f"{request['run_id']}.intent.json").write_bytes(
        boundary._canonical(submission_intent)
    )
    (receipt_root / f"{request['run_id']}.json").write_bytes(
        boundary._canonical(submission_receipt)
    )
    intent_path = observation_root / f"{request['authorization_id']}.intent.json"
    receipt_path = observation_root / f"{request['authorization_id']}.json"
    observation_intent = {
        "request_sha256": boundary._sha(boundary._canonical(request)),
        "submission_intent_sha256": intent_sha,
        "submission_receipt_sha256": receipt_sha,
        "status": "intent",
        "pre_hashes": boundary._hashes(baseline),
    }
    intent_path.write_bytes(boundary._canonical(observation_intent))
    monkeypatch.setattr(boundary.host, "REVIEW_FINAL_REVIEW_SUBMISSION_RECEIPTS_ROOT", receipt_root)
    monkeypatch.setattr(boundary.phase79, "_run_directory", lambda _: run)
    monkeypatch.setattr(boundary, "_snapshot", lambda *_args, **_kwargs: baseline)
    def fake_record(path: Path) -> tuple[dict[str, object], str]:
        if path == intent_path:
            return observation_intent, boundary._sha(boundary._canonical(observation_intent))
        if path.name.endswith(".intent.json"):
            return submission_intent, intent_sha
        return submission_receipt, receipt_sha

    monkeypatch.setattr(
        boundary,
        "_record",
        fake_record,
    )

    assert boundary._replay(request, intent_path, receipt_path) is None

    monkeypatch.setattr(
        boundary, "_snapshot", lambda *_args, **_kwargs: {**baseline, "run-state.json": b"changed"}
    )
    with pytest.raises(boundary.FinalReviewObservationError):
        boundary._replay(request, intent_path, receipt_path)
