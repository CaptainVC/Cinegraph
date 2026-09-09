from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event, Thread

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
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.submit_calls = 0

    def submit(
        self,
        request_filename: str,
        request_bytes: bytes,
        completion_window: str,
        metadata: dict[str, str],
    ) -> BatchSubmission:
        self.submit_calls += 1
        if self.fail:
            raise RuntimeError("ambiguous provider outcome")
        assert request_filename == "primary-part-0002-requests.jsonl"
        assert request_bytes == b'{"custom_id":"part-2"}\n'
        assert completion_window == "24h"
        assert metadata["part"] == "2"
        return BatchSubmission("batch-2", "file-2", "validating")

    def retrieve(self, *_: object) -> object:
        raise AssertionError("next-primary submission must not observe")

    def download_file(self, *_: object) -> str:
        raise AssertionError("next-primary submission must not download")


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


def _checkpoint(
    *,
    status: SpeakerReviewRunStatus = SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED,
    completed: int = 1,
    parts: int = 2,
    batch_ids: tuple[str, ...] | None = None,
    input_ids: tuple[str, ...] | None = None,
) -> SpeakerReviewRunState:
    batches = batch_ids if batch_ids is not None else tuple(
        f"batch-{index}" for index in range(1, completed + 1)
    )
    inputs = input_ids if input_ids is not None else tuple(
        f"file-{index}" for index in range(1, completed + 1)
    )
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
        actual_primary_cost_usd=0.0,
        actual_adjudication_cost_usd=0.0,
        primary_part_count=parts,
        primary_completed_part_count=completed,
        primary_batch_id=batches[-1] if batches else None,
        primary_input_file_id=inputs[-1] if inputs else None,
        primary_batch_ids=batches,
        primary_input_file_ids=inputs,
    )


def _next_request(run_directory: Path) -> None:
    (run_directory / "primary-part-0002-requests.jsonl").write_bytes(
        b'{"custom_id":"part-2"}\n'
    )


def test_submits_exactly_one_next_part_and_stops_at_primary_submitted(
    tmp_path: Path,
) -> None:
    gateway = SubmissionGateway()
    _next_request(tmp_path)

    updated = _workflow(gateway).submit_next_primary_part(tmp_path, _checkpoint())

    assert gateway.submit_calls == 1
    assert updated.status is SpeakerReviewRunStatus.PRIMARY_SUBMITTED
    assert updated.primary_completed_part_count == 1
    assert updated.primary_batch_ids == ("batch-1", "batch-2")
    assert updated.primary_input_file_ids == ("file-1", "file-2")
    assert updated.primary_batch_id == "batch-2"
    assert updated.primary_input_file_id == "file-2"
    assert load_run_state(tmp_path / "run-state.json") == updated
    assert not (tmp_path / "primary-verdicts.jsonl").exists()
    assert not (tmp_path / "primary-decisions.jsonl").exists()
    assert not (tmp_path / "adjudication-part-0001-requests.jsonl").exists()


def test_all_primary_parts_completed_is_identity_without_gateway_or_writes(
    tmp_path: Path,
) -> None:
    gateway = SubmissionGateway(fail=True)
    state = _checkpoint(completed=1, parts=1)

    assert _workflow(gateway).submit_next_primary_part(tmp_path, state) is state
    assert gateway.submit_calls == 0
    assert list(tmp_path.iterdir()) == []


def test_matching_completed_journal_repairs_state_without_resubmission(
    tmp_path: Path,
) -> None:
    _next_request(tmp_path)
    first_gateway = SubmissionGateway()
    first = _workflow(first_gateway).submit_next_primary_part(tmp_path, _checkpoint())
    assert first_gateway.submit_calls == 1

    replay_gateway = SubmissionGateway(fail=True)
    repaired = _workflow(replay_gateway).submit_next_primary_part(tmp_path, _checkpoint())

    assert replay_gateway.submit_calls == 0
    assert repaired.primary_batch_ids == first.primary_batch_ids
    assert repaired.primary_input_file_ids == first.primary_input_file_ids


def test_ambiguous_intent_blocks_every_automatic_resubmission(tmp_path: Path) -> None:
    _next_request(tmp_path)
    gateway = SubmissionGateway(fail=True)
    workflow = _workflow(gateway)

    with pytest.raises(RuntimeError, match="operator reconciliation"):
        workflow.submit_next_primary_part(tmp_path, _checkpoint())
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        workflow.submit_next_primary_part(tmp_path, _checkpoint())

    assert gateway.submit_calls == 1


def test_changed_next_request_cannot_reuse_completed_journal(tmp_path: Path) -> None:
    _next_request(tmp_path)
    gateway = SubmissionGateway()
    workflow = _workflow(gateway)
    workflow.submit_next_primary_part(tmp_path, _checkpoint())
    (tmp_path / "primary-part-0002-requests.jsonl").write_bytes(
        b'{"custom_id":"changed"}\n'
    )

    with pytest.raises(RuntimeError, match="operator reconciliation"):
        workflow.submit_next_primary_part(tmp_path, _checkpoint())
    assert gateway.submit_calls == 1


@pytest.mark.parametrize(
    "state",
    [
        _checkpoint(status=SpeakerReviewRunStatus.PREPARED, completed=0, batch_ids=(), input_ids=()),
        _checkpoint(
            status=SpeakerReviewRunStatus.PRIMARY_SUBMITTED,
            completed=0,
            batch_ids=("batch-1",),
            input_ids=("file-1",),
        ),
        _checkpoint(completed=0, batch_ids=(), input_ids=()),
        _checkpoint(completed=2, parts=1),
        _checkpoint(
            completed=2,
            parts=3,
            batch_ids=("batch-1", "batch-1"),
            input_ids=("file-1", "file-2"),
        ),
        replace(_checkpoint(), primary_batch_id="batch-other"),
    ],
)
def test_invalid_checkpoint_shapes_fail_before_provider_access(
    tmp_path: Path,
    state: SpeakerReviewRunState,
) -> None:
    gateway = SubmissionGateway(fail=True)

    with pytest.raises(RuntimeError, match="completed-part checkpoint"):
        _workflow(gateway).submit_next_primary_part(tmp_path, state)
    assert gateway.submit_calls == 0


def test_post_submit_replay_is_idempotent_but_phase61_state_is_not(
    tmp_path: Path,
) -> None:
    gateway = SubmissionGateway(fail=True)
    post_submit = _checkpoint(
        status=SpeakerReviewRunStatus.PRIMARY_SUBMITTED,
        completed=1,
        parts=3,
        batch_ids=("batch-1", "batch-2"),
        input_ids=("file-1", "file-2"),
    )

    assert _workflow(gateway).submit_next_primary_part(tmp_path, post_submit) is post_submit
    assert gateway.submit_calls == 0


def test_concurrent_next_submission_allows_only_one_provider_create(
    tmp_path: Path,
) -> None:
    entered = Event()
    release = Event()

    class BlockingGateway(SubmissionGateway):
        def submit(
            self,
            request_filename: str,
            request_bytes: bytes,
            completion_window: str,
            metadata: dict[str, str],
        ) -> BatchSubmission:
            entered.set()
            assert release.wait(timeout=5)
            return super().submit(
                request_filename,
                request_bytes,
                completion_window,
                metadata,
            )

    gateway = BlockingGateway()
    workflow = _workflow(gateway)
    _next_request(tmp_path)
    first_errors: list[BaseException] = []

    def submit_first() -> None:
        try:
            workflow.submit_next_primary_part(tmp_path, _checkpoint())
        except BaseException as error:  # pragma: no cover - asserted below
            first_errors.append(error)

    thread = Thread(target=submit_first)
    thread.start()
    assert entered.wait(timeout=5)
    try:
        with pytest.raises(RuntimeError, match="operator reconciliation"):
            workflow.submit_next_primary_part(tmp_path, _checkpoint())
    finally:
        release.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert first_errors == []
    assert gateway.submit_calls == 1


def test_completed_journal_recovers_after_state_write_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = SubmissionGateway()
    workflow = _workflow(gateway)
    _next_request(tmp_path)
    original_save = workflow_module.save_run_state

    def crash_state_write(*_: object) -> None:
        raise OSError("simulated state persistence crash")

    monkeypatch.setattr(workflow_module, "save_run_state", crash_state_write)
    with pytest.raises(OSError, match="state persistence crash"):
        workflow.submit_next_primary_part(tmp_path, _checkpoint())
    assert gateway.submit_calls == 1

    monkeypatch.setattr(workflow_module, "save_run_state", original_save)
    recovered = workflow.submit_next_primary_part(tmp_path, _checkpoint())

    assert gateway.submit_calls == 1
    assert recovered.status is SpeakerReviewRunStatus.PRIMARY_SUBMITTED
    assert recovered.primary_batch_ids == ("batch-1", "batch-2")
