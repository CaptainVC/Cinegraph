from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from scripts import private_speaker_review_first_adjudication_submission_contract as contract
from scripts import run_private_speaker_review_first_adjudication as coordinator

RUN_ID = "speaker-review-0123456789abcdef"
AUTHORIZATION_ID = "123e4567-e89b-42d3-a456-426614174000"
ARCHIVE_SHA = "a" * 64


def _request() -> dict[str, object]:
    return {
        "archive_sha256": ARCHIVE_SHA,
        "authorization_id": AUTHORIZATION_ID,
        "maximum_authorized_cost_microusd": 5_000_000,
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "schema_version": contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
    }


def _state(status: str, *, updated_at: str = "2026-01-01T00:00:00+00:00") -> dict[str, object]:
    return {
        "schema_version": 5,
        "run_id": RUN_ID,
        "status": status,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": updated_at,
        "candidate_count": 2,
        "primary_model": "gpt-5.6-luna",
        "adjudication_model": "gpt-5.6-terra",
        "prompt_version": "speaker-review-v1",
        "maximum_cost_usd": 5.0,
        "estimated_primary_cost_usd": 0.25,
        "actual_primary_cost_usd": 0.10,
        "actual_adjudication_cost_usd": 0.0,
        "adjudication_part_count": 1,
        "adjudication_batch_id": None,
        "adjudication_input_file_id": None,
        "adjudication_batch_ids": [],
        "adjudication_input_file_ids": [],
    }


def _install_coordinator_fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    run = tmp_path / "run"
    run.mkdir()
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    monkeypatch.setattr(coordinator, "RECEIPTS_ROOT", receipts)
    if os.name == "posix":
        monkeypatch.setattr(coordinator, "ROOT_UID", os.geteuid())
        monkeypatch.setattr(coordinator, "ROOT_GID", os.getegid())
    monkeypatch.setattr(coordinator, "_validate_authorization", lambda _: "c" * 64)
    monkeypatch.setattr(
        coordinator,
        "_preparation",
        lambda _: (
            {
                "config_sha": "d" * 64,
                "image": "cinegraph@sha256:" + "e" * 64,
                "release_sha": "f" * 40,
            },
            "b" * 64,
        ),
    )
    monkeypatch.setattr(coordinator.phase68, "_source_workspace", lambda _: None)
    monkeypatch.setattr(
        coordinator.predecessor,
        "_active_binding",
        lambda: ("f" * 40, "cinegraph@sha256:" + "e" * 64, "d" * 64),
    )
    monkeypatch.setattr(coordinator, "_run", lambda _: run)
    monkeypatch.setattr(coordinator, "_directory", lambda *args, **kwargs: None)
    request_raw = coordinator._canonical(_request())
    artifacts = {"candidates.jsonl": b"candidate\n"}
    outputs = {"primary-part-0001-output.jsonl": b"output\n"}
    derived = {"adjudication-part-0001-requests.jsonl": b"request\n"}
    prepared = _state("adjudication_prepared")
    submitted = _state("adjudication_submitted", updated_at="2026-01-01T00:01:00+00:00")
    submitted.update(
        adjudication_batch_id="batch-1",
        adjudication_input_file_id="file-1",
        adjudication_batch_ids=["batch-1"],
        adjudication_input_file_ids=["file-1"],
    )
    calls = {"inventory": 0, "worker": 0, "force_submitted": False}

    def inventory(_run):
        calls["inventory"] += 1
        replay = calls["force_submitted"] or calls["worker"] > 0
        state = submitted if replay else prepared
        journals: dict[str, bytes] = {}
        if replay:
            binding = {
                "batch_endpoint": "/v1/responses",
                "completion_window": "24h",
                "part": 1,
                "prompt_version": "speaker-review-v1",
                "request_sha256": coordinator._sha(request_raw),
                "run_id": RUN_ID,
                "schema_version": 1,
                "stage": "adjudication",
            }
            journals = {
                coordinator.INTENT_NAME: coordinator._canonical(
                    {"binding": binding, "status": "intent"}
                ),
                coordinator.COMPLETED_NAME: coordinator._canonical(
                    {
                        "batch_id": "batch-1",
                        "binding": binding,
                        "input_file_id": "file-1",
                        "status": "validating",
                    }
                ),
            }
        files = {
            coordinator.STATE_NAME: coordinator._canonical(state),
            coordinator.REQUEST_NAME: request_raw,
            **journals,
        }
        return files, artifacts, journals, outputs, {"state": state, "derived": derived}

    monkeypatch.setattr(coordinator, "_inventory", inventory)
    monkeypatch.setattr(
        coordinator, "_phase68", lambda *args: ("1" * 64, {"status": "adjudication_prepared"})
    )

    def worker(*args):
        calls["worker"] += 1
        status = "already_submitted" if calls["worker"] > 1 else "submitted"
        return {
            "actual_primary_cost_microusd": 100_000,
            "adjudication_part_count": 1,
            "estimated_adjudication_cost_microusd": 100,
            "operation": contract.OPERATION,
            "purpose": contract.PURPOSE,
            "run_id": RUN_ID,
            "run_status": "adjudication_submitted",
            "season_number": contract.SEASON_NUMBER,
            "status": status,
            "submitted_part_count": 1,
        }

    monkeypatch.setattr(coordinator, "_run_worker", worker)
    return calls


def test_fresh_then_replay_writes_intent_and_normalized_submitted_receipt(tmp_path, monkeypatch):
    calls = _install_coordinator_fakes(tmp_path, monkeypatch)
    first = coordinator.process_request(_request())
    second = coordinator.process_request(_request())
    assert first["status"] == "submitted"
    assert second["status"] == "already_submitted"
    assert calls["worker"] == 2
    intent = json.loads((coordinator.RECEIPTS_ROOT / f"{RUN_ID}.intent.json").read_text())
    receipt = json.loads((coordinator.RECEIPTS_ROOT / f"{RUN_ID}.json").read_text())
    assert intent["pre_updated_at"] == "2026-01-01T00:00:00+00:00"
    assert receipt["status"] == "receipt"
    assert receipt["result"]["status"] == "submitted"


def test_submitted_state_without_receipt_is_rejected(tmp_path, monkeypatch):
    calls = _install_coordinator_fakes(tmp_path, monkeypatch)
    calls["force_submitted"] = True
    with pytest.raises(coordinator.FirstAdjudicationSubmissionError):
        coordinator.process_request(_request())


def test_submitted_state_repairs_missing_receipt_without_resubmission(tmp_path, monkeypatch):
    calls = _install_coordinator_fakes(tmp_path, monkeypatch)
    coordinator.process_request(_request())
    receipt_path = coordinator.RECEIPTS_ROOT / f"{RUN_ID}.json"
    receipt_path.unlink()

    replay = coordinator.process_request(_request())

    assert replay["status"] == "already_submitted"
    assert calls["worker"] == 2
    repaired = json.loads(receipt_path.read_text())
    assert repaired["status"] == "receipt"
    assert repaired["result"]["status"] == "submitted"


def test_tampered_receipt_and_post_worker_immutable_groups_are_rejected(tmp_path, monkeypatch):
    _install_coordinator_fakes(tmp_path, monkeypatch)
    coordinator.process_request(_request())
    receipt_path = coordinator.RECEIPTS_ROOT / f"{RUN_ID}.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["authorization_id"] = "00000000-0000-4000-8000-000000000000"
    receipt_path.write_text(coordinator._canonical(receipt).decode())
    with pytest.raises(coordinator.FirstAdjudicationSubmissionError):
        coordinator.process_request(_request())


def test_submission_journal_rejects_boolean_schema_version():
    request_raw = coordinator._canonical(_request())
    binding = {
        "batch_endpoint": "/v1/responses",
        "completion_window": "24h",
        "part": 1,
        "prompt_version": "speaker-review-v1",
        "request_sha256": coordinator._sha(request_raw),
        "run_id": RUN_ID,
        "schema_version": True,
        "stage": "adjudication",
    }
    completed = {
        "batch_id": "batch-1",
        "binding": binding,
        "input_file_id": "file-1",
        "status": "validating",
    }
    state = _state("adjudication_submitted")
    state.update(
        adjudication_batch_id="batch-1",
        adjudication_input_file_id="file-1",
        adjudication_batch_ids=["batch-1"],
        adjudication_input_file_ids=["file-1"],
    )

    with pytest.raises(
        coordinator.FirstAdjudicationSubmissionError,
        match="adjudication journals invalid",
    ):
        coordinator._validate_submission_journals(
            state,
            {
                coordinator.INTENT_NAME: coordinator._canonical(
                    {"binding": binding, "status": "intent"}
                ),
                coordinator.COMPLETED_NAME: coordinator._canonical(completed),
            },
            coordinator._sha(request_raw),
        )


def test_worker_args_mounts_current_digest_parent_and_expected_service(tmp_path):
    run = tmp_path / ("sha256-" + "a" * 64) / "review-runs" / RUN_ID
    command = coordinator._worker_args(_request(), run, {"binding": "x"})
    assert f"{run.parent}:{coordinator.host.REVIEW_FIRST_ADJUDICATION_RUNS_TARGET}:rw" in command
    assert coordinator.host.REVIEW_FIRST_ADJUDICATION_COMPOSE_SERVICE in command
