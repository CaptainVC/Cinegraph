from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts import private_speaker_review_next_final_review_host_contract as host
from scripts import private_speaker_review_next_final_review_submission_client as client
from scripts import private_speaker_review_next_final_review_submission_contract as contract
from scripts import run_private_speaker_review_next_final_review as coordinator


def test_boundary_uses_part_two_command_and_isolated_compose_profile() -> None:
    assert host.REVIEW_NEXT_FINAL_REVIEW_COMMAND == contract.COMMAND
    assert host.REVIEW_NEXT_FINAL_REVIEW_COMPOSE_PROFILE == (
        "corpus-speaker-review-submit-next-final-review"
    )
    assert host.REVIEW_NEXT_FINAL_REVIEW_CONTAINER_COMMAND == (
        "python",
        "scripts/submit_next_final_private_speaker_review_workspace.py",
    )
    assert client.submit_next_final_review_part is client.submit_next_final_review


def test_dispatch_and_forced_helper_are_narrow() -> None:
    dispatch = Path("deploy/remote/review-dispatch.sh").read_text(encoding="utf-8")
    helper = Path(
        "deploy/remote/submit-next-final-private-speaker-review.sh"
    ).read_text(encoding="utf-8")
    compose = Path("deploy/compose.yaml").read_text(encoding="utf-8")
    assert contract.COMMAND in dispatch
    assert host.REVIEW_NEXT_FINAL_REVIEW_HELPER_PATH.as_posix() in dispatch
    assert "python3 -I -S -B" in helper
    assert "OPENAI_API_KEY" not in helper
    assert 'check "$path" file 644' in helper
    assert host.REVIEW_NEXT_FINAL_REVIEW_COMPOSE_SERVICE in compose
    assert "/run/secrets/openai_api_key" not in helper


def test_coordinator_uses_nested_phase79_inventory_and_hardened_compose() -> None:
    source = Path("scripts/run_private_speaker_review_next_final_review.py").read_text(
        encoding="utf-8"
    )
    assert coordinator._hashes({"run-state.json": b"x", "a-requests.jsonl": b"y"}) == {
        "artifacts": {"a-requests.jsonl": coordinator._sha(b"y")},
        "journals": {},
        "outputs": {},
        "derived": {},
    }
    assert '"--pull", "never"' in source
    assert '"--no-TTY"' in source
    assert "ThreadPoolExecutor" in source
    assert "_assert_worker_identity" in source
    for forbidden in ("OPENAI_BASE_URL", "OPENAI_ORG", "OPENAI_PROJECT", "SSL_CERT_FILE"):
        assert forbidden in Path(
            "scripts/submit_next_final_private_speaker_review_workspace.py"
        ).read_text(encoding="utf-8")


def _transition_state() -> tuple[dict[str, object], dict[str, object]]:
    before = {
        "run_id": "speaker-review-0123456789abcdef",
        "final_review_completed_part_count": 1,
        "actual_final_review_cost_usd": 0.0,
        "final_review_batch_ids": ["batch-1"],
        "final_review_input_file_ids": ["input-1"],
        "final_review_batch_id": "batch-1",
        "final_review_input_file_id": "input-1",
        "status": "final_review_submitted",
        "updated_at": "one",
        "final_review_part_count": 2,
    }
    after = dict(before)
    after.update(
        {
            "updated_at": "two",
            "status": "final_review_submitted",
            "final_review_batch_ids": ["batch-1", "batch-2"],
            "final_review_input_file_ids": ["input-1", "input-2"],
            "final_review_batch_id": "batch-2",
            "final_review_input_file_id": "input-2",
        }
    )
    return before, after


def test_root_accepts_only_state_and_two_part_journals_transition() -> None:
    before_state, after_state = _transition_state()
    coordinator._validate_transition(
        {"run-state.json": b"old", "artifact.json": b"same"},
        {
            "run-state.json": b"new",
            "artifact.json": b"same",
            ".final-review-part-0002-submission-intent.json": b"intent",
            ".final-review-part-0002-submission-completed.json": b"completed",
        },
        before_state,
        after_state,
    )
    with pytest.raises(coordinator.NextFinalReviewSubmissionError):
        coordinator._validate_transition(
            {"run-state.json": b"old", "artifact.json": b"same"},
            {"run-state.json": b"new", "artifact.json": b"changed"},
            before_state,
            after_state,
        )


def _root_state(count: int) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "final_review_submitted",
        "final_review_completed_part_count": 1,
        "final_review_part_count": 2,
        "maximum_cost_usd": 5.0,
        "actual_primary_cost_usd": 0.0,
        "actual_adjudication_cost_usd": 0.0,
        "actual_final_review_cost_usd": 0.0,
        "actual_total_cost_usd": 0.0,
        "primary_batch_ids": ["primary-batch-1"],
        "adjudication_batch_ids": ["adjudication-batch-1"],
        "final_review_batch_ids": [f"batch-{index}" for index in range(1, count + 1)],
        "primary_input_file_ids": ["primary-input-1"],
        "adjudication_input_file_ids": ["adjudication-input-1"],
        "final_review_input_file_ids": [f"input-{index}" for index in range(1, count + 1)],
        "primary_batch_id": "primary-batch-1",
        "primary_input_file_id": "primary-input-1",
        "adjudication_batch_id": "adjudication-batch-1",
        "adjudication_input_file_id": "adjudication-input-1",
        "final_review_batch_id": f"batch-{count}",
        "final_review_input_file_id": f"input-{count}",
    }


@pytest.mark.parametrize("count", [1, 2])
def test_root_state_accepts_predecessor_and_paid_retry_array_shapes(count: int) -> None:
    state = _root_state(count)
    raw = json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n"
    assert coordinator._state({"run-state.json": raw})["final_review_batch_ids"] == state[
        "final_review_batch_ids"
    ]


def test_root_state_rejects_cross_stage_provider_id_collision() -> None:
    state = _root_state(2)
    state["final_review_batch_ids"] = ["batch-1", "primary-batch-1"]
    state["final_review_batch_id"] = "primary-batch-1"
    raw = json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n"

    with pytest.raises(coordinator.NextFinalReviewSubmissionError):
        coordinator._state({"run-state.json": raw})


def test_root_state_rejects_scalar_provider_id_alias_mismatch() -> None:
    state = _root_state(1)
    state["primary_batch_id"] = "batch-1"
    raw = json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n"

    with pytest.raises(coordinator.NextFinalReviewSubmissionError):
        coordinator._state({"run-state.json": raw})


def test_root_state_rejects_nonzero_final_review_cost() -> None:
    state = _root_state(1)
    state["actual_final_review_cost_usd"] = 0.25
    state["actual_total_cost_usd"] = 0.25
    raw = json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n"

    with pytest.raises(coordinator.NextFinalReviewSubmissionError):
        coordinator._state({"run-state.json": raw})


def test_root_cost_conversion_rounds_up_to_whole_microusd() -> None:
    assert coordinator._micros(0.0000001) == 1


def test_coordinator_paid_state_without_receipt_is_provider_free_reconciliation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = {
        "schema_version": coordinator.SPEAKER_REVIEW_SCHEMA_VERSION,
        "status": "final_review_submitted",
        "run_id": "speaker-review-0123456789abcdef",
        "final_review_completed_part_count": 1,
        "final_review_part_count": 2,
        "maximum_cost_usd": 5.0,
        "actual_primary_cost_usd": 0.0,
        "actual_adjudication_cost_usd": 0.0,
        "actual_final_review_cost_usd": 0.0,
        "actual_total_cost_usd": 0.0,
        "primary_model": coordinator.SPEAKER_PRIMARY_REVIEW_MODEL,
        "adjudication_model": coordinator.SPEAKER_ADJUDICATION_MODEL,
        "final_review_model": coordinator.SPEAKER_FINAL_REVIEW_MODEL,
        "prompt_version": coordinator.SPEAKER_REVIEW_PROMPT_VERSION,
        "primary_batch_ids": ["primary-batch-1"],
        "adjudication_batch_ids": ["adjudication-batch-1"],
        "final_review_batch_ids": ["batch-1", "batch-2"],
        "primary_input_file_ids": ["primary-input-1"],
        "adjudication_input_file_ids": ["adjudication-input-1"],
        "final_review_input_file_ids": ["input-1", "input-2"],
        "primary_batch_id": "primary-batch-1",
        "primary_input_file_id": "primary-input-1",
        "adjudication_batch_id": "adjudication-batch-1",
        "adjudication_input_file_id": "adjudication-input-1",
        "final_review_batch_id": "batch-2",
        "final_review_input_file_id": "input-2",
    }
    raw = json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n"
    before = {
        "run-state.json": raw,
        ".final-review-part-0002-submission-intent.json": b"intent",
        ".final-review-part-0002-submission-completed.json": b"completed",
    }
    request = {
        "archive_sha256": "a" * 64,
        "authorization_id": "12345678-1234-4234-8234-123456789abc",
        "maximum_authorized_cost_microusd": 5_000_000,
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": state["run_id"],
        "schema_version": contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
    }
    phase80 = {
        "result": {
            "estimated_final_review_cost_microusd": 0,
            "final_review_part_count": 2,
            "maximum_authorized_cost_microusd": 5_000_000,
        },
        "post_hashes": coordinator._observation_hashes(before),
    }
    monkeypatch.setattr(coordinator, "RECEIPT_ROOT", tmp_path)
    monkeypatch.setattr(coordinator, "_directory", lambda *args, **kwargs: None)
    monkeypatch.setattr(coordinator, "_run", lambda request: tmp_path)
    monkeypatch.setattr(coordinator, "_snapshot", lambda run: before)
    monkeypatch.setattr(coordinator, "_validate_inventory_names", lambda state, contents: None)
    monkeypatch.setattr(coordinator, "_validate_predecessors", lambda request, contents, state: ("p80", "p79", "auth", phase80))
    monkeypatch.setattr(coordinator.phase79, "_active_runtime_binding", lambda: ("release", "image", "config"))
    monkeypatch.setattr(coordinator, "_worker_once", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("provider worker")))

    result = coordinator.process_request(request)

    assert result["status"] == "reconciliation_required"


def test_predecessor_chain_rejects_phase78_tampering(monkeypatch: pytest.MonkeyPatch) -> None:
    request = {
        "authorization_id": "12345678-1234-4234-8234-123456789abc",
        "run_id": "speaker-review-0123456789abcdef",
    }
    phase80 = {
        "schema_version": coordinator.observation_contract.PROTOCOL_VERSION,
        "status": "receipt",
        "intent_sha256": "p80-intent",
        "request_sha256": coordinator._sha(
            coordinator.observation_contract.canonical_json(
                {
                    **request,
                    "operation": coordinator.observation_contract.OPERATION,
                }
            )
        ),
        "submission_receipt_sha256": "p79-receipt",
        "result": {
            "actual_adjudication_cost_microusd": 0,
            "actual_final_review_cost_microusd": 0,
            "actual_primary_cost_microusd": 0,
            "estimated_final_review_cost_microusd": 0,
            "final_review_part_count": 2,
            "run_id": request["run_id"],
            "maximum_authorized_cost_microusd": 5_000_000,
            "state_maximum_cost_microusd": 5_000_000,
            "operation": coordinator.observation_contract.OPERATION,
            "purpose": coordinator.observation_contract.PURPOSE,
            "season_number": coordinator.observation_contract.SEASON_NUMBER,
            "run_status": "final_review_submitted",
            "status": "observed",
            "final_review_completed_part_count": 1,
        },
    }
    records = {
        ("phase80", f"{request['authorization_id']}.json"): (phase80, "p80-receipt"),
        ("phase80", f"{request['authorization_id']}.intent.json"): (
            {
                    "authorization_id": request["authorization_id"],
                    "run_id": request["run_id"],
                    "request_sha256": phase80["request_sha256"],
                    "status": "intent",
            },
            "p80-intent",
        ),
        ("phase79", f"{request['run_id']}.json"): (
            {
                "schema_version": coordinator.phase79.contract.PROTOCOL_VERSION,
                "status": "receipt",
                "request_sha256": coordinator._sha(
                    coordinator.phase79.contract.canonical_json(
                        {**request, "operation": coordinator.phase79.contract.OPERATION}
                    )
                ),
                "result": {
                    "actual_primary_cost_microusd": 0,
                    "actual_adjudication_cost_microusd": 0,
                    "estimated_final_review_cost_microusd": 0,
                    "final_review_completed_part_count": 0,
                    "final_review_part_count": 2,
                    "operation": coordinator.phase79.contract.OPERATION,
                    "purpose": coordinator.phase79.contract.PURPOSE,
                    "run_id": request["run_id"],
                    "run_status": "final_review_submitted",
                    "season_number": coordinator.phase79.contract.SEASON_NUMBER,
                    "status": "submitted",
                    "submitted_part_count": 1,
                },
                "intent_sha256": "p79-intent",
                "authorization_claim_sha256": "claim-receipt",
            },
            "p79-receipt",
        ),
        ("phase79", f"authorization-{request['authorization_id']}.claim.json"): (
            {"claim": "valid"},
            "claim-receipt",
        ),
        ("phase79", f"{request['run_id']}.intent.json"): (
            {
                "phase78_processing_intent_sha256": "p78-intent",
                "phase78_processing_receipt_sha256": "p78-receipt",
            },
            "p79-intent",
        ),
        ("phase78", f"{request['run_id']}.intent.json"): ({"intent": True}, "p78-intent"),
        ("phase78", f"{request['run_id']}.json"): ({"intent_sha256": "tampered"}, "p78-receipt"),
    }

    def fake_receipt(root: Path, name: str):
        phase = "phase80" if root == coordinator.host.REVIEW_PHASE80_RECEIPTS_ROOT else "phase79" if root == coordinator.host.REVIEW_PHASE79_RECEIPTS_ROOT else "phase78"
        return records[(phase, name)]

    monkeypatch.setattr(coordinator, "_receipt", fake_receipt)
    monkeypatch.setattr(coordinator.phase79, "_validate_authorization", lambda value: "auth-digest")
    monkeypatch.setattr(coordinator.phase79, "_claim_payload", lambda value, digest: {"claim": "valid"})

    with pytest.raises(coordinator.NextFinalReviewSubmissionError):
        coordinator._validate_predecessors(request, {}, {"final_review_part_count": 2})
