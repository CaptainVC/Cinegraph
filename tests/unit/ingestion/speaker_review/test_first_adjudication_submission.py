from __future__ import annotations

from pathlib import Path

import pytest

from cinegraph.config import DEFAULT_SPEAKER_REVIEW_CONFIGURATION
from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review.workflow import (
    SpeakerReviewRunState,
    SpeakerReviewWorkflow,
    load_run_state,
)
from cinegraph.ports.llm.speaker_review_batch_gateway import BatchSubmission


class Gateway:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def submit(self, filename: str, payload: bytes, window: str, metadata: dict[str, str]) -> BatchSubmission:
        self.calls += 1
        assert filename == "adjudication-part-0001-requests.jsonl"
        assert payload == b"{}\n"
        assert window == DEFAULT_SPEAKER_REVIEW_CONFIGURATION.batch_completion_window
        assert metadata["part"] == "1"
        if self.fail:
            raise RuntimeError("ambiguous")
        return BatchSubmission("batch-a", "file-a", "validating")


def state(status: SpeakerReviewRunStatus = SpeakerReviewRunStatus.ADJUDICATION_PREPARED) -> SpeakerReviewRunState:
    return SpeakerReviewRunState(
        schema_version=5,
        run_id="speaker-review-0123456789abcdef",
        status=status,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
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
        adjudication_part_count=2,
    )


def workflow(gateway: Gateway, *, expected: str | None = None) -> SpeakerReviewWorkflow:
    return SpeakerReviewWorkflow(
        gateway=gateway,  # type: ignore[arg-type]
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        final_review_model="gpt-5.6-sol",
        primary_reasoning_effort="low",
        adjudication_reasoning_effort="medium",
        final_review_reasoning_effort="high",
        expected_first_adjudication_request_sha256=expected,
    )


def test_submits_only_first_adjudication_part(tmp_path: Path) -> None:
    request = tmp_path / "adjudication-part-0001-requests.jsonl"
    request.write_bytes(b"{}\n")
    gateway = Gateway()
    updated = workflow(gateway).submit_first_adjudication(tmp_path, state())
    assert gateway.calls == 1
    assert updated.status is SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED
    assert updated.adjudication_batch_ids == ("batch-a",)
    assert updated.adjudication_input_file_ids == ("file-a",)
    assert updated.adjudication_completed_part_count == 0
    assert load_run_state(tmp_path / "run-state.json") == updated


def test_exact_submitted_replay_is_unchanged_without_provider_call(tmp_path: Path) -> None:
    request = tmp_path / "adjudication-part-0001-requests.jsonl"
    request.write_bytes(b"{}\n")
    first = workflow(Gateway()).submit_first_adjudication(tmp_path, state())
    replay_gateway = Gateway(fail=True)
    replay = workflow(replay_gateway).submit_first_adjudication(tmp_path, first)
    assert replay is first
    assert replay_gateway.calls == 0


def test_ambiguous_intent_is_not_retried(tmp_path: Path) -> None:
    (tmp_path / "adjudication-part-0001-requests.jsonl").write_bytes(b"{}\n")
    gateway = Gateway(fail=True)
    operation = workflow(gateway)
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        operation.submit_first_adjudication(tmp_path, state())
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        operation.submit_first_adjudication(tmp_path, state())
    assert gateway.calls == 1


@pytest.mark.parametrize("bad", [SpeakerReviewRunStatus.PREPARED, SpeakerReviewRunStatus.PRIMARY_SUBMITTED])
def test_only_prepared_checkpoint_is_accepted(tmp_path: Path, bad: SpeakerReviewRunStatus) -> None:
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        workflow(Gateway(fail=True)).submit_first_adjudication(tmp_path, state(bad))
