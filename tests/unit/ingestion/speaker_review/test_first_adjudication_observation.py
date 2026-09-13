from __future__ import annotations

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


class Gateway:
    def __init__(self, status: str, *, mismatched: bool = False) -> None:
        self.status = status
        self.mismatched = mismatched
        self.retrieved: list[str] = []
        self.downloaded: list[str] = []
        self.submissions = 0

    def submit(self, *_args: object, **_kwargs: object) -> object:
        self.submissions += 1
        raise AssertionError("the observer must never submit")

    def retrieve(self, batch_id: str) -> BatchSnapshot:
        self.retrieved.append(batch_id)
        return BatchSnapshot(
            batch_id="different" if self.mismatched else batch_id,
            status=self.status,
            output_file_id="output-1" if self.status == "completed" else None,
            error_file_id="error-1" if self.status == "failed" else None,
            total_requests=1,
            completed_requests=1 if self.status == "completed" else 0,
            failed_requests=1 if self.status == "failed" else 0,
        )

    def download_file(self, file_id: str) -> str:
        self.downloaded.append(file_id)
        return "provider-error\n" if file_id == "error-1" else "{}\n"


def _state(
    status: SpeakerReviewRunStatus = SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED,
) -> SpeakerReviewRunState:
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
        primary_part_count=2,
        primary_completed_part_count=2,
        adjudication_part_count=2,
        adjudication_completed_part_count=(
            1 if status is SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED else 0
        ),
        adjudication_batch_id="batch-a",
        adjudication_input_file_id="input-a",
        adjudication_batch_ids=("batch-a",),
        adjudication_input_file_ids=("input-a",),
    )


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


def test_waiting_reads_provider_once_and_changes_nothing(tmp_path: Path) -> None:
    gateway = Gateway("in_progress")
    state = _state()
    assert _workflow(gateway).observe_first_adjudication(tmp_path, state) is state
    assert gateway.retrieved == ["batch-a"]
    assert gateway.downloaded == []
    assert gateway.submissions == 0


def test_completed_downloads_only_part_one_and_stops(tmp_path: Path) -> None:
    gateway = Gateway("completed")
    updated = _workflow(gateway).observe_first_adjudication(tmp_path, _state())
    assert updated.status is SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED
    assert updated.adjudication_completed_part_count == 1
    assert gateway.retrieved == ["batch-a"]
    assert gateway.downloaded == ["output-1"]
    assert gateway.submissions == 0
    assert (tmp_path / "adjudication-part-0001-output.jsonl").read_bytes() == b"{}\n"
    assert not (tmp_path / "adjudication-part-0002-output.jsonl").exists()
    assert not (tmp_path / "adjudication-verdicts.jsonl").exists()
    assert load_run_state(tmp_path / "run-state.json") == updated


def test_terminal_failure_is_persisted_without_submission(tmp_path: Path) -> None:
    gateway = Gateway("failed")
    updated = _workflow(gateway).observe_first_adjudication(tmp_path, _state())
    assert updated.status is SpeakerReviewRunStatus.FAILED
    assert updated.adjudication_completed_part_count == 0
    assert gateway.downloaded == ["error-1"]
    assert gateway.submissions == 0


def test_completed_replay_accepts_missing_optional_error_file(tmp_path: Path) -> None:
    (tmp_path / "adjudication-part-0001-output.jsonl").write_bytes(b"{}\n")
    gateway = Gateway("completed")
    state = _state(SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED)
    assert _workflow(gateway).observe_first_adjudication(tmp_path, state) is state
    assert gateway.retrieved == []


@pytest.mark.parametrize(
    "state",
    [
        _state(SpeakerReviewRunStatus.ADJUDICATION_PREPARED),
        replace(_state(), adjudication_batch_ids=()),
    ],
)
def test_invalid_checkpoint_reconciles_before_provider(
    tmp_path: Path, state: SpeakerReviewRunState
) -> None:
    gateway = Gateway("completed")
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        _workflow(gateway).observe_first_adjudication(tmp_path, state)
    assert gateway.retrieved == []


def test_mismatched_provider_snapshot_requires_reconciliation(tmp_path: Path) -> None:
    gateway = Gateway("completed", mismatched=True)
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        _workflow(gateway).observe_first_adjudication(tmp_path, _state())
    assert gateway.submissions == 0
