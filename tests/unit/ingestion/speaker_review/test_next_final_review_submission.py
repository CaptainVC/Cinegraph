from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cinegraph.config import DEFAULT_SPEAKER_REVIEW_CONFIGURATION
from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review import workflow as workflow_module
from cinegraph.ingestion.speaker_review.workflow import (
    SpeakerReviewRunState,
    SpeakerReviewWorkflow,
    load_run_state,
)
from cinegraph.ports.llm.speaker_review_batch_gateway import BatchSubmission


class SubmissionGateway:
    def __init__(
        self,
        *,
        fail: bool = False,
        returned_batch_id: str | None = None,
        returned_input_file_id: str | None = None,
    ) -> None:
        self.fail = fail
        self.returned_batch_id = returned_batch_id
        self.returned_input_file_id = returned_input_file_id
        self.parts: list[int] = []

    def submit(
        self,
        request_filename: str,
        request_bytes: bytes,
        completion_window: str,
        metadata: dict[str, str],
    ) -> BatchSubmission:
        part = int(metadata["part"])
        self.parts.append(part)
        if self.fail:
            raise RuntimeError("ambiguous provider outcome")
        assert request_filename == f"final-review-part-{part:04d}-requests.jsonl"
        assert request_bytes == f'{{"custom_id":"part-{part}"}}\n'.encode()
        assert completion_window == "24h"
        return BatchSubmission(
            self.returned_batch_id or f"batch-{part}",
            self.returned_input_file_id or f"file-{part}",
            "validating",
        )

    def retrieve(self, *_: object) -> object:
        raise AssertionError("next-final-review submission must not observe")

    def download_file(self, *_: object) -> str:
        raise AssertionError("next-final-review submission must not download")


def _workflow(gateway: SubmissionGateway) -> SpeakerReviewWorkflow:
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


def _state(*, completed: int = 1, parts: int = 3) -> SpeakerReviewRunState:
    ids = tuple(f"batch-{part}" for part in range(1, completed + 1))
    input_ids = tuple(f"file-{part}" for part in range(1, completed + 1))
    return SpeakerReviewRunState(
        schema_version=5,
        run_id="speaker-review-0123456789abcdef",
        status=SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        candidate_count=2,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        prompt_version="speaker-review-v1",
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=0.25,
        actual_primary_cost_usd=0.1,
        actual_adjudication_cost_usd=0.2,
        final_review_model="gpt-5.6-sol",
        actual_final_review_cost_usd=0.0,
        primary_part_count=1,
        primary_completed_part_count=1,
        final_review_part_count=parts,
        final_review_completed_part_count=completed,
        final_review_batch_id=ids[-1],
        final_review_input_file_id=input_ids[-1],
        final_review_batch_ids=ids,
        final_review_input_file_ids=input_ids,
    )


def _checkpoint(run: Path, *, completed: int = 1, parts: int = 3) -> SpeakerReviewRunState:
    gateway = SubmissionGateway()
    workflow = _workflow(gateway)
    for part in range(1, parts + 1):
        (run / f"final-review-part-{part:04d}-requests.jsonl").write_bytes(
            f'{{"custom_id":"part-{part}"}}\n'.encode()
        )
    state = _state(completed=completed, parts=parts)
    for part in range(1, completed + 1):
        workflow._submit_part(  # noqa: SLF001
            run_directory=run,
            state=state,
            stage="final-review",
            part_index=part - 1,
        )
        (run / f"final-review-part-{part:04d}-output.jsonl").write_bytes(
            f'{{"part":{part}}}\n'.encode()
        )
    return state


def test_submits_exactly_one_next_part_and_keeps_count_and_cost(tmp_path: Path) -> None:
    state = _checkpoint(tmp_path)
    gateway = SubmissionGateway()

    updated = _workflow(gateway).submit_next_final_review_part(tmp_path, state)

    assert gateway.parts == [2]
    assert updated.status is SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED
    assert updated.final_review_completed_part_count == 1
    assert updated.actual_final_review_cost_usd == 0.0
    assert updated.final_review_batch_ids == ("batch-1", "batch-2")
    assert updated.final_review_input_file_ids == ("file-1", "file-2")
    assert load_run_state(tmp_path / "run-state.json") == updated
    assert not (tmp_path / "final-review-part-0002-output.jsonl").exists()


def test_generic_transition_submits_part_k_plus_one(tmp_path: Path) -> None:
    state = _checkpoint(tmp_path, completed=2, parts=4)
    gateway = SubmissionGateway()

    updated = _workflow(gateway).submit_next_final_review_part(tmp_path, state)

    assert gateway.parts == [3]
    assert updated.final_review_completed_part_count == 2
    assert updated.final_review_batch_ids == ("batch-1", "batch-2", "batch-3")


def test_matching_completed_journal_replays_without_provider(tmp_path: Path) -> None:
    state = _checkpoint(tmp_path)
    first_gateway = SubmissionGateway()
    submitted = _workflow(first_gateway).submit_next_final_review_part(tmp_path, state)

    replay_gateway = SubmissionGateway(fail=True)
    assert (
        _workflow(replay_gateway).submit_next_final_review_part(tmp_path, submitted)
        is submitted
    )
    assert replay_gateway.parts == []


def test_replay_rejects_output_or_error_for_newly_submitted_part(tmp_path: Path) -> None:
    state = _checkpoint(tmp_path)
    submitted = _workflow(SubmissionGateway()).submit_next_final_review_part(
        tmp_path, state
    )
    (tmp_path / "final-review-part-0002-output.jsonl").write_bytes(b"unexpected\n")

    with pytest.raises(RuntimeError, match="completed-part checkpoint"):
        _workflow(SubmissionGateway(fail=True)).submit_next_final_review_part(
            tmp_path, submitted
        )

    (tmp_path / "final-review-part-0002-output.jsonl").unlink()
    (tmp_path / "final-review-part-0002-api-errors.jsonl").write_bytes(
        b"unexpected\n"
    )
    with pytest.raises(RuntimeError, match="completed-part checkpoint"):
        _workflow(SubmissionGateway(fail=True)).submit_next_final_review_part(
            tmp_path, submitted
        )


def test_completed_journal_repairs_state_after_state_write_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _checkpoint(tmp_path)
    gateway = SubmissionGateway()
    workflow = _workflow(gateway)

    monkeypatch.setattr(
        workflow_module,
        "save_run_state",
        lambda *_: (_ for _ in ()).throw(OSError("state crash")),
    )
    with pytest.raises(OSError, match="state crash"):
        workflow.submit_next_final_review_part(tmp_path, state)
    monkeypatch.undo()

    recovered = workflow.submit_next_final_review_part(tmp_path, state)
    assert gateway.parts == [2]
    assert recovered.final_review_batch_ids == ("batch-1", "batch-2")


def test_malformed_prior_output_fails_before_provider(tmp_path: Path) -> None:
    state = _checkpoint(tmp_path)
    (tmp_path / "final-review-part-0001-output.jsonl").unlink()
    gateway = SubmissionGateway()

    with pytest.raises(RuntimeError, match="completed-part checkpoint"):
        _workflow(gateway).submit_next_final_review_part(tmp_path, state)
    assert gateway.parts == []


def test_all_parts_completed_is_identity_without_provider(tmp_path: Path) -> None:
    state = _state(completed=1, parts=1)
    gateway = SubmissionGateway(fail=True)

    with pytest.raises(RuntimeError, match="completed-part checkpoint"):
        _workflow(gateway).submit_next_final_review_part(tmp_path, state)
    assert gateway.parts == []


def test_all_parts_completed_validates_every_durable_artifact(tmp_path: Path) -> None:
    state = _checkpoint(tmp_path, completed=1, parts=1)
    gateway = SubmissionGateway(fail=True)

    assert _workflow(gateway).submit_next_final_review_part(tmp_path, state) is state
    assert gateway.parts == []


def test_provider_batch_id_collision_with_primary_is_rejected(
    tmp_path: Path,
) -> None:
    state = replace(
        _checkpoint(tmp_path),
        primary_batch_id="batch-2",
        primary_batch_ids=("batch-2",),
    )
    gateway = SubmissionGateway(returned_batch_id="batch-2")

    with pytest.raises(RuntimeError, match="completed-part checkpoint"):
        _workflow(gateway).submit_next_final_review_part(tmp_path, state)
    assert gateway.parts == [2]
    assert not (tmp_path / "run-state.json").exists()


def test_active_output_or_error_evidence_blocks_submission(tmp_path: Path) -> None:
    state = _checkpoint(tmp_path)
    (tmp_path / "final-review-part-0002-api-errors.jsonl").write_bytes(b"unexpected\n")
    gateway = SubmissionGateway()

    with pytest.raises(RuntimeError, match="completed-part checkpoint"):
        _workflow(gateway).submit_next_final_review_part(tmp_path, state)
    assert gateway.parts == []


def test_invalid_alias_shape_fails_closed(tmp_path: Path) -> None:
    state = _checkpoint(tmp_path)
    gateway = SubmissionGateway()

    with pytest.raises(RuntimeError, match="completed-part checkpoint"):
        _workflow(gateway).submit_next_final_review_part(
            tmp_path,
            replace(state, final_review_batch_id="different"),
        )
    assert gateway.parts == []
