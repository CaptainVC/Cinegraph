from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
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


def _state(*, completed: int = 0, part_count: int = 3) -> SpeakerReviewRunState:
    return SpeakerReviewRunState(
        schema_version=5,
        run_id="speaker-review-0123456789abcdef",
        status=SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED,
        created_at="2026-09-23T00:00:00+00:00",
        updated_at="2026-09-23T00:00:00+00:00",
        candidate_count=1,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        prompt_version="speaker-review-v1",
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=0.1,
        actual_primary_cost_usd=0.1,
        actual_adjudication_cost_usd=0.1,
        primary_part_count=1,
        primary_completed_part_count=1,
        adjudication_part_count=1,
        adjudication_completed_part_count=1,
        adjudication_batch_id="adjudication-batch",
        adjudication_input_file_id="adjudication-file",
        adjudication_batch_ids=("adjudication-batch",),
        adjudication_input_file_ids=("adjudication-file",),
        final_review_part_count=part_count,
        final_review_completed_part_count=completed,
        final_review_batch_id="final-batch-1",
        final_review_input_file_id="final-file-1",
        final_review_batch_ids=("final-batch-1",),
        final_review_input_file_ids=("final-file-1",),
    )


class Gateway:
    def __init__(
        self, status: str, *, wrong_id: bool = False, error_file: bool | None = None
    ) -> None:
        self.status = status
        self.wrong_id = wrong_id
        self.error_file = status == "failed" if error_file is None else error_file
        self.retrieved: list[str] = []
        self.downloaded: list[str] = []

    def retrieve(self, batch_id: str) -> BatchSnapshot:
        self.retrieved.append(batch_id)
        return BatchSnapshot(
            batch_id="wrong" if self.wrong_id else batch_id,
            status=self.status,
            output_file_id="out" if self.status == "completed" else None,
            error_file_id="err" if self.error_file else None,
            total_requests=1,
            completed_requests=1 if self.status == "completed" else 0,
            failed_requests=1 if self.status == "failed" else 0,
        )

    def download_file(self, file_id: str) -> str:
        self.downloaded.append(file_id)
        return "private provider evidence\n"


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


def test_success_downloads_only_part_one_and_retains_submitted_status(
    tmp_path: Path,
) -> None:
    gateway = Gateway("completed")
    before = _state()
    updated = _workflow(gateway).observe_final_review_part(tmp_path, before)

    assert updated.status is SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED
    assert updated.final_review_completed_part_count == 1
    assert updated.final_review_batch_ids == before.final_review_batch_ids
    assert gateway.retrieved == ["final-batch-1"]
    assert gateway.downloaded == ["out"]
    assert (tmp_path / "final-review-part-0001-output.jsonl").read_bytes() == (
        b"private provider evidence\n"
    )
    assert load_run_state(tmp_path / "run-state.json") == updated
    assert not (tmp_path / "final-review-part-0002-output.jsonl").exists()


def test_waiting_has_no_mutation_and_replay_is_provider_free(tmp_path: Path) -> None:
    gateway = Gateway("in_progress")
    before = _state()
    assert _workflow(gateway).observe_final_review_part(tmp_path, before) is before
    assert list(tmp_path.iterdir()) == []

    (tmp_path / "final-review-part-0001-output.jsonl").write_bytes(b"output\n")
    replay_gateway = Gateway("completed")
    replay_state = replace(before, final_review_completed_part_count=1)
    assert _workflow(replay_gateway).observe_final_review_part(tmp_path, replay_state) is replay_state
    assert replay_gateway.retrieved == []
    assert replay_gateway.downloaded == []


@pytest.mark.parametrize(
    "state",
    [
        replace(_state(), status=SpeakerReviewRunStatus.FINAL_REVIEW_PREPARED),
        replace(_state(), final_review_completed_part_count=2),
        replace(_state(), final_review_batch_ids=("final-batch-1", "final-batch-2")),
        replace(_state(), final_review_input_file_ids=("final-file-1", "final-file-2")),
        replace(_state(), final_review_batch_id="other"),
    ],
)
def test_invalid_shape_reconciles_without_provider(tmp_path: Path, state: SpeakerReviewRunState) -> None:
    gateway = Gateway("completed")
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        _workflow(gateway).observe_final_review_part(tmp_path, state)
    assert gateway.retrieved == []


def test_existing_partial_output_requires_reconciliation(tmp_path: Path) -> None:
    (tmp_path / "final-review-part-0001-output.jsonl").write_bytes(b"partial")
    gateway = Gateway("completed")
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        _workflow(gateway).observe_final_review_part(tmp_path, _state())
    assert gateway.retrieved == []


def test_terminal_failure_persists_failed_state_and_error_evidence(tmp_path: Path) -> None:
    gateway = Gateway("failed")
    with pytest.raises(RuntimeError, match="ended with status failed"):
        _workflow(gateway).observe_final_review_part(tmp_path, _state())
    persisted = load_run_state(tmp_path / "run-state.json")
    assert persisted.status is SpeakerReviewRunStatus.FAILED
    assert persisted.final_review_completed_part_count == 0
    assert (tmp_path / "terminal-api-errors.jsonl").read_bytes() == b"private provider evidence\n"


def test_terminal_failure_without_provider_error_file_persists_sanitized_evidence(
    tmp_path: Path,
) -> None:
    gateway = Gateway("expired", error_file=False)
    with pytest.raises(RuntimeError, match="ended with status expired"):
        _workflow(gateway).observe_final_review_part(tmp_path, _state())

    persisted = load_run_state(tmp_path / "run-state.json")
    expected = (
        "{\"batch_id_sha256\":\""
        + sha256(b"final-batch-1").hexdigest()
        + "\",\"schema_version\":1,\"status\":\"expired\"}\n"
    ).encode("ascii")
    evidence = (tmp_path / "terminal-api-errors.jsonl").read_bytes()
    assert persisted.status is SpeakerReviewRunStatus.FAILED
    assert persisted.final_review_completed_part_count == 0
    assert evidence == expected
    assert b"final-batch-1" not in evidence
    assert gateway.downloaded == []


def test_snapshot_id_mismatch_reconciles(tmp_path: Path) -> None:
    gateway = Gateway("completed", wrong_id=True)
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        _workflow(gateway).observe_final_review_part(tmp_path, _state())
    assert gateway.downloaded == []
