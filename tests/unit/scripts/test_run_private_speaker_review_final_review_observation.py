from __future__ import annotations

import json
import os
import uuid
from hashlib import sha256
from pathlib import Path

import pytest
from scripts import run_private_speaker_review_final_review as phase79
from scripts import run_private_speaker_review_final_review_observation as boundary
from scripts.private_speaker_review_final_review_observation_contract import (
    OPERATION,
    PROTOCOL_VERSION,
    PURPOSE,
    SEASON_NUMBER,
)


class State:
    def __init__(self, values: dict[str, object]) -> None:
        self.values = values

    def to_dict(self) -> dict[str, object]:
        return dict(self.values)


def _state(*, status: str = "final_review_submitted", completed: int = 0) -> dict[str, object]:
    value: dict[str, object] = dict.fromkeys(phase79.STATE_KEYS)
    for key in (
        "schema_version",
        "candidate_count",
        "primary_part_count",
        "primary_completed_part_count",
        "adjudication_part_count",
        "adjudication_completed_part_count",
        "final_review_part_count",
        "final_review_completed_part_count",
        "final_review_retry_count",
        "accepted_by_consensus",
        "accepted_by_adjudication",
        "accepted_by_final_review",
        "accepted_by_human",
        "needs_human",
    ):
        value[key] = 0
    value.update(
        {
            "schema_version": 1,
            "run_id": "speaker-review-0123456789abcdef",
            "status": status,
            "created_at": "created",
            "updated_at": "updated",
            "candidate_count": 1,
            "primary_model": "model-primary",
            "adjudication_model": "model-adjudication",
            "prompt_version": "prompt-v1",
            "maximum_cost_usd": 2.0,
            "estimated_primary_cost_usd": 0.1,
            "actual_primary_cost_usd": 0.1,
            "actual_adjudication_cost_usd": 0.2,
            "final_review_model": "model-final",
            "actual_final_review_cost_usd": 0.0,
            "actual_total_cost_usd": 0.3,
            "primary_part_count": 1,
            "primary_completed_part_count": 1,
            "adjudication_part_count": 1,
            "adjudication_completed_part_count": 1,
            "final_review_part_count": 2,
            "final_review_completed_part_count": completed,
            "needs_human": 1,
            "primary_batch_ids": ["primary-batch"],
            "primary_input_file_ids": ["primary-input"],
            "adjudication_batch_ids": ["adjudication-batch"],
            "adjudication_input_file_ids": ["adjudication-input"],
            "final_review_batch_ids": ["final-batch"],
            "final_review_input_file_ids": ["final-input"],
            "primary_batch_id": "primary-batch",
            "primary_input_file_id": "primary-input",
            "adjudication_batch_id": "adjudication-batch",
            "adjudication_input_file_id": "adjudication-input",
            "final_review_batch_id": "final-batch",
            "final_review_input_file_id": "final-input",
        }
    )
    return value


def _state_bytes(value: dict[str, object]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    request = {
        "archive_sha256": "a" * 64,
        "authorization_id": str(uuid.uuid4()),
        "maximum_authorized_cost_microusd": 5_000_000,
        "operation": OPERATION,
        "purpose": PURPOSE,
        "run_id": "speaker-review-0123456789abcdef",
        "schema_version": PROTOCOL_VERSION,
        "season_number": SEASON_NUMBER,
    }
    run = tmp_path / "run"
    run.mkdir()
    initial = _state()
    before = {
        "run-state.json": _state_bytes(initial),
        "final-review-part-0001-requests.jsonl": b"req\n",
    }
    state_holder = {"contents": before}
    submission_intent = {
        "run_id": request["run_id"],
        "authorization_claim_sha256": "claim-sha",
        "phase78_processing_intent_sha256": "p78-intent-sha",
        "phase78_processing_receipt_sha256": "p78-receipt-sha",
        "estimated_final_review_cost_microusd": 300,
        "maximum_authorized_cost_microusd": request["maximum_authorized_cost_microusd"],
        "state_maximum_cost_microusd": 2_000_000,
    }
    phase79_receipt = {"intent_sha256": "p79-intent-sha"}
    claim = {"claimed": True}
    observation_root = tmp_path / "observation-receipts"
    observation_root.mkdir()
    submission_root = tmp_path / "submission-receipts"
    submission_root.mkdir()
    phase78_root = tmp_path / "phase78-receipts"
    phase78_root.mkdir()
    for key, value in (
        ("REVIEW_FINAL_REVIEW_OBSERVATION_RECEIPTS_ROOT", observation_root),
        ("REVIEW_FINAL_REVIEW_SUBMISSION_RECEIPTS_ROOT", submission_root),
        ("REVIEW_PHASE78_RECEIPTS_ROOT", phase78_root),
    ):
        monkeypatch.setattr(boundary.host, key, value)
    current_owner = (
        int(getattr(os, "getuid", lambda: 0)()),
        int(getattr(os, "getgid", lambda: 0)()),
    )
    monkeypatch.setattr(boundary, "ROOT_OWNER", current_owner)
    monkeypatch.setattr(boundary.phase79, "_directory", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(boundary.phase79, "_run_directory", lambda _: run)
    monkeypatch.setattr(
        boundary,
        "_submission_chain",
        lambda _: (
            run,
            before,
            State(initial),
            submission_intent,
            "p79-receipt-sha",
            "p79-intent-sha",
        ),
    )
    monkeypatch.setattr(boundary, "_snapshot", lambda *_args, **_kwargs: state_holder["contents"])
    monkeypatch.setattr(boundary, "_phase80_bindings", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(boundary.phase79, "_validate_authorization", lambda _: "auth-sha")
    monkeypatch.setattr(boundary.phase79, "_claim_payload", lambda *_args: claim)
    original_record = boundary._record

    def record(path: Path):
        name = path.name
        if path.parent == submission_root:
            if name.endswith(".intent.json"):
                return submission_intent, "p79-intent-sha"
            if name.startswith("authorization-"):
                return claim, "claim-sha"
            return phase79_receipt, "p79-receipt-sha"
        if path.parent == phase78_root:
            return (
                ({"status": "intent"}, "p78-intent-sha")
                if name.endswith(".intent.json")
                else ({"intent_sha256": "p78-intent-sha"}, "p78-receipt-sha")
            )
        return original_record(path)

    monkeypatch.setattr(boundary, "_record", record)
    return request, state_holder, observation_root, before


def _worker_result(status: str, state: dict[str, object]) -> dict[str, object]:
    return boundary._aggregate(
        {
            "run_id": "speaker-review-0123456789abcdef",
            "maximum_authorized_cost_microusd": 5_000_000,
        },
        state,
        status=status,
        estimated=300,
        state_maximum=2_000_000,
    )


def test_waiting_can_be_observed_later_and_success_replay_is_provider_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request, state_holder, _, before = _harness(tmp_path, monkeypatch)
    calls = 0

    def worker(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _worker_result("waiting", _state())
        state = _state(completed=1)
        state["updated_at"] = "observed"
        state_holder["contents"] = {
            **before,
            "run-state.json": _state_bytes(state),
            "final-review-part-0001-output.jsonl": b"{}\n",
        }
        return _worker_result("observed", state)

    monkeypatch.setattr(boundary, "_worker_once", worker)
    first = boundary.process_request(request)
    assert first["status"] == "waiting"
    assert not (tmp_path / "observation-receipts" / f"{request['authorization_id']}.json").exists()
    assert boundary.process_request(request)["status"] == "observed"
    assert boundary.process_request(request)["status"] == "observed"
    assert calls == 2


def test_failure_without_provider_error_file_has_durable_marker_and_receipt_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request, state_holder, _, before = _harness(tmp_path, monkeypatch)
    calls = 0

    def worker(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        state = _state(status="failed")
        state["updated_at"] = "failed"
        state_holder["contents"] = {
            **before,
            "run-state.json": _state_bytes(state),
            "terminal-api-errors.jsonl": (
                json.dumps(
                    {
                        "batch_id_sha256": sha256(b"final-batch-1").hexdigest(),
                        "schema_version": 1,
                        "status": "failed",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("ascii"),
        }
        return _worker_result("failed", state)

    monkeypatch.setattr(boundary, "_worker_once", worker)
    result = boundary.process_request(request)
    assert result["status"] == "failed"
    assert state_holder["contents"]["terminal-api-errors.jsonl"]
    assert boundary.process_request(request)["status"] == "failed"
    assert calls == 1


def test_partial_mutation_without_root_receipt_requires_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request, state_holder, observation_root, before = _harness(tmp_path, monkeypatch)

    def worker(*_args, **_kwargs):
        state_holder["contents"] = {**before, "request.jsonl": b"tampered"}
        return _worker_result("waiting", _state())

    monkeypatch.setattr(boundary, "_worker_once", worker)
    with pytest.raises(boundary.FinalReviewObservationError):
        boundary.process_request(request)
    receipt = observation_root / f"{request['authorization_id']}.json"
    assert not receipt.exists()
    with pytest.raises(boundary.FinalReviewObservationError, match="reconciliation"):
        boundary.process_request(request)


def test_compose_arguments_bind_run_and_protocol_without_ambient_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = {
        "archive_sha256": "a" * 64,
        "authorization_id": str(uuid.uuid4()),
        "maximum_authorized_cost_microusd": 5_000_000,
        "operation": OPERATION,
        "purpose": PURPOSE,
        "run_id": "speaker-review-0123456789abcdef",
        "schema_version": PROTOCOL_VERSION,
        "season_number": SEASON_NUMBER,
    }
    run = tmp_path / request["run_id"]
    run.mkdir()
    bindings = {"CINEGRAPH_SPEAKER_REVIEW_EXPECTED_SUBMISSION_RECEIPT_SHA256": "b" * 64}
    captured: dict[str, object] = {}

    def capture(_request, worker_run, worker_bindings):
        captured["args"] = phase79._worker_args(_request, worker_run, worker_bindings)
        return {}

    monkeypatch.setattr(phase79, "_run_worker", capture)
    boundary._worker_once(request, run, bindings)
    args = captured["args"]
    assert isinstance(args, list)
    assert "--profile" in args
    assert "corpus-speaker-review-observe-final-review" in args
    assert "corpus-speaker-review-observe-final-review" in args
    assert f"{run.as_posix()}:/review-workspace/review-runs/{request['run_id']}:rw" in args
    assert any(
        item.startswith("CINEGRAPH_SPEAKER_REVIEW_EXPECTED_SUBMISSION_RECEIPT_SHA256=")
        for item in args
    )
    assert not any("OPENAI_API_KEY=" in item or "HTTP_PROXY=" in item for item in args)
    assert "OPENAI_API_KEY" not in phase79._safe_env()
    compose = Path("deploy/compose.yaml").read_text(encoding="utf-8")
    profile = compose.split("  corpus-speaker-review-observe-final-review:", 1)[1].split(
        "\n  postgres:", 1
    )[0]
    assert "network_mode: none" not in profile
    assert "source: openai_api_key" in profile and "mode: 0400" in profile
    assert "read_only: true" in profile and "cap_drop:" in profile
