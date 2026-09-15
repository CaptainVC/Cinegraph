from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from cinegraph.config import DEFAULT_SPEAKER_REVIEW_CONFIGURATION
from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review.workflow import (
    SpeakerReviewRunState,
    SpeakerReviewWorkflow,
    load_run_state,
)
from cinegraph.ports.llm.speaker_review_batch_gateway import BatchSnapshot


def _state(
    status: SpeakerReviewRunStatus = SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED,
    *,
    completed: int = 1,
    total: int = 3,
    ids: tuple[str, ...] | None = None,
    input_ids: tuple[str, ...] | None = None,
) -> SpeakerReviewRunState:
    batch_ids = ids or tuple(f"batch-{part}" for part in range(1, completed + 2))
    file_ids = input_ids or tuple(f"file-{part}" for part in range(1, completed + 2))
    if status is SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED:
        batch_ids = batch_ids[:completed]
        file_ids = file_ids[:completed]
    return SpeakerReviewRunState(
        schema_version=5,
        run_id="speaker-review-0123456789abcdef",
        status=status,
        created_at="2026-09-13T00:00:00+00:00",
        updated_at="2026-09-13T00:00:00+00:00",
        candidate_count=2,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        prompt_version="speaker-review-v1",
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=0.25,
        actual_primary_cost_usd=0.10,
        actual_adjudication_cost_usd=0.0,
        primary_part_count=1,
        primary_completed_part_count=1,
        adjudication_part_count=total,
        adjudication_completed_part_count=completed,
        adjudication_batch_id=batch_ids[-1],
        adjudication_input_file_id=file_ids[-1],
        adjudication_batch_ids=batch_ids,
        adjudication_input_file_ids=file_ids,
    )


class Gateway:
    def __init__(self, status: str, *, mismatch: bool = False) -> None:
        self.status = status
        self.mismatch = mismatch
        self.retrieved: list[str] = []
        self.downloaded: list[str] = []

    def retrieve(self, batch_id: str) -> BatchSnapshot:
        self.retrieved.append(batch_id)
        return BatchSnapshot(
            batch_id="wrong-batch" if self.mismatch else batch_id,
            status=self.status,
            output_file_id="output-id" if self.status == "completed" else None,
            error_file_id="error-id" if self.status == "failed" else None,
            total_requests=1,
            completed_requests=1 if self.status == "completed" else 0,
            failed_requests=1 if self.status == "failed" else 0,
        )

    def download_file(self, file_id: str) -> str:
        self.downloaded.append(file_id)
        return "provider-error\n" if file_id == "error-id" else "{}\n"


def _workflow(gateway: Gateway) -> SpeakerReviewWorkflow:
    return SpeakerReviewWorkflow(
        gateway=gateway,  # type: ignore[arg-type]
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        final_review_model="gpt-5.6-sol",
        primary_reasoning_effort="low",
        adjudication_reasoning_effort="medium",
        final_review_reasoning_effort="high",
    )


def test_part_two_success_is_the_only_provider_read_and_exact_transition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = Gateway("completed")
    monkeypatch.setattr(SpeakerReviewWorkflow, "_validate_next_adjudication_observation_checkpoint", lambda *_: None)
    before = _state()
    updated = _workflow(gateway).observe_next_adjudication(tmp_path, before)

    assert updated.status is SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED
    assert updated.adjudication_completed_part_count == 2
    assert gateway.retrieved == ["batch-2"]
    assert gateway.downloaded == ["output-id"]
    assert (tmp_path / "adjudication-part-0002-output.jsonl").read_bytes() == b"{}\n"
    assert load_run_state(tmp_path / "run-state.json") == updated
    assert not (tmp_path / "adjudication-part-0003-output.jsonl").exists()


def test_generic_k_two_to_three_observation_uses_last_provider_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = Gateway("completed")
    monkeypatch.setattr(SpeakerReviewWorkflow, "_validate_next_adjudication_observation_checkpoint", lambda *_: None)
    updated = _workflow(gateway).observe_next_adjudication(tmp_path, _state(completed=2))

    assert updated.adjudication_completed_part_count == 3
    assert gateway.retrieved == ["batch-3"]
    assert (tmp_path / "adjudication-part-0003-output.jsonl").exists()


def test_waiting_and_terminal_failure_do_not_submit_or_advance_part_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SpeakerReviewWorkflow, "_validate_next_adjudication_observation_checkpoint", lambda *_: None)
    waiting_gateway = Gateway("in_progress")
    waiting_state = _state()
    assert _workflow(waiting_gateway).observe_next_adjudication(tmp_path, waiting_state) is waiting_state
    assert waiting_gateway.downloaded == []
    assert list(tmp_path.iterdir()) == []

    failed_gateway = Gateway("failed")
    failed = _workflow(failed_gateway).observe_next_adjudication(tmp_path, _state())
    assert failed.status is SpeakerReviewRunStatus.FAILED
    assert failed.adjudication_completed_part_count == 1
    assert failed_gateway.downloaded == ["error-id"]
    assert (tmp_path / "terminal-api-errors.jsonl").exists()


@pytest.mark.parametrize(
    "state",
    [
        _state(SpeakerReviewRunStatus.ADJUDICATION_PREPARED),
        _state(completed=0),
        _state(ids=("batch-1", "batch-1")),
        _state(input_ids=("file-1", "file-1")),
        replace(_state(), adjudication_batch_id="not-the-active-id"),
        replace(_state(), adjudication_input_file_id="not-the-active-id"),
    ],
)
def test_wrong_status_count_duplicate_and_mismatched_ids_reconcile_before_provider(
    tmp_path: Path, state: SpeakerReviewRunState
) -> None:
    gateway = Gateway("completed")
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        _workflow(gateway).observe_next_adjudication(tmp_path, state)
    assert gateway.retrieved == []


def test_provider_snapshot_batch_id_mismatch_reconciles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SpeakerReviewWorkflow, "_validate_next_adjudication_observation_checkpoint", lambda *_: None)
    gateway = Gateway("completed", mismatch=True)
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        _workflow(gateway).observe_next_adjudication(tmp_path, _state())
    assert gateway.downloaded == []


def _active_checkpoint(run: Path, state: SpeakerReviewRunState) -> None:
    request = b"{}\n"
    (run / "adjudication-part-0002-requests.jsonl").write_bytes(request)
    binding = {
        "schema_version": 1,
        "request_sha256": hashlib.sha256(request).hexdigest(),
        "run_id": state.run_id,
        "stage": "adjudication",
        "part": 2,
        "prompt_version": state.prompt_version,
        "batch_endpoint": DEFAULT_SPEAKER_REVIEW_CONFIGURATION.batch_endpoint,
        "completion_window": DEFAULT_SPEAKER_REVIEW_CONFIGURATION.batch_completion_window,
    }
    for suffix, payload in (
        ("intent", {"binding": binding, "status": "intent"}),
        (
            "completed",
            {
                "binding": binding,
                "batch_id": state.adjudication_batch_id,
                "input_file_id": state.adjudication_input_file_id,
                "status": "completed",
            },
        ),
    ):
        (run / f".adjudication-part-0002-submission-{suffix}.json").write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
            newline="\n",
        )


def _completed_part_checkpoint(
    run: Path,
    state: SpeakerReviewRunState,
    *,
    part: int = 1,
) -> None:
    request = b"{}\n"
    stem = f"adjudication-part-{part:04d}"
    (run / f"{stem}-requests.jsonl").write_bytes(request)
    binding = {
        "schema_version": 1,
        "request_sha256": hashlib.sha256(request).hexdigest(),
        "run_id": state.run_id,
        "stage": "adjudication",
        "part": part,
        "prompt_version": state.prompt_version,
        "batch_endpoint": DEFAULT_SPEAKER_REVIEW_CONFIGURATION.batch_endpoint,
        "completion_window": DEFAULT_SPEAKER_REVIEW_CONFIGURATION.batch_completion_window,
    }
    (run / f".{stem}-submission-intent.json").write_bytes(
        (
            json.dumps(
                {"binding": binding, "status": "intent"},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    )
    (run / f".{stem}-submission-completed.json").write_bytes(
        (
            json.dumps(
                {
                    "binding": binding,
                    "batch_id": f"batch-{part}",
                    "input_file_id": f"file-{part}",
                    "status": "completed",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    )
    (run / f"{stem}-output.jsonl").write_bytes(b"{}\n")


@pytest.mark.parametrize("tamper", ["request", "binding", "provider_id"])
def test_completed_journal_request_binding_and_provider_id_tamper_reconciles(
    tmp_path: Path, tamper: str
) -> None:
    state = _state()
    _completed_part_checkpoint(tmp_path, state)
    _active_checkpoint(tmp_path, state)
    if tamper == "request":
        (tmp_path / "adjudication-part-0001-requests.jsonl").write_bytes(b'{"changed":true}\n')
    elif tamper == "binding":
        path = tmp_path / ".adjudication-part-0001-submission-intent.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["binding"]["part"] = 99
        path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    else:
        path = tmp_path / ".adjudication-part-0001-submission-completed.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["batch_id"] = "provider-tampered"
        path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        _workflow(Gateway("completed")).observe_next_adjudication(tmp_path, state)


@pytest.mark.parametrize("name", ["adjudication-part-0002-output.jsonl", "adjudication-part-0002-api-errors.jsonl"])
def test_active_output_or_api_error_preexistence_and_symlink_reconcile(
    tmp_path: Path, name: str
) -> None:
    state = _state()
    _completed_part_checkpoint(tmp_path, state)
    _active_checkpoint(tmp_path, state)
    (tmp_path / name).write_bytes(b"preexisting\n")
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        _workflow(Gateway("completed"))._validate_next_adjudication_observation_checkpoint(tmp_path, state)

    if os.name != "nt":
        (tmp_path / name).unlink()
        target = tmp_path / "target"
        target.write_bytes(b"secret\n")
        (tmp_path / name).symlink_to(target)
        with pytest.raises(RuntimeError, match="operator reconciliation"):
            _workflow(Gateway("completed"))._validate_next_adjudication_observation_checkpoint(tmp_path, state)


def test_completed_replay_is_provider_free_and_authenticates_every_completed_part(
    tmp_path: Path,
) -> None:
    state = _state(SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED, completed=2)
    for part in (1, 2):
        _completed_part_checkpoint(tmp_path, state, part=part)
    gateway = Gateway("completed")
    assert _workflow(gateway).observe_next_adjudication(tmp_path, state) is state
    assert gateway.retrieved == []


def test_valid_checkpoint_observation_and_replay_authenticate_real_journals(
    tmp_path: Path,
) -> None:
    state = _state()
    _completed_part_checkpoint(tmp_path, state)
    _active_checkpoint(tmp_path, state)
    gateway = Gateway("completed")
    workflow = _workflow(gateway)

    observed = workflow.observe_next_adjudication(tmp_path, state)
    assert observed.adjudication_completed_part_count == 2
    assert workflow.observe_next_adjudication(tmp_path, observed) is observed
    assert gateway.retrieved == ["batch-2"]
    assert gateway.downloaded == ["output-id"]


def test_completed_replay_rejects_a_future_output_without_provider_access(
    tmp_path: Path,
) -> None:
    state = _state(SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED, completed=2)
    for part in (1, 2):
        _completed_part_checkpoint(tmp_path, state, part=part)
    (tmp_path / "adjudication-part-0003-output.jsonl").write_bytes(b"{}\n")
    gateway = Gateway("completed")

    with pytest.raises(RuntimeError, match="operator reconciliation"):
        _workflow(gateway).observe_next_adjudication(tmp_path, state)

    assert gateway.retrieved == []
