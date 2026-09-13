from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
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
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
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
        assert request_filename == f"adjudication-part-{part:04d}-requests.jsonl"
        assert request_bytes == f'{{"custom_id":"part-{part}"}}\n'.encode()
        assert completion_window == "24h"
        return BatchSubmission(f"batch-{part}", f"file-{part}", "validating")

    def retrieve(self, *_: object) -> object:
        raise AssertionError("next-adjudication submission must not observe")

    def download_file(self, *_: object) -> str:
        raise AssertionError("next-adjudication submission must not download")


def _workflow(
    gateway: SubmissionGateway,
    *,
    expected_request_sha256: str | None = None,
) -> SpeakerReviewWorkflow:
    return SpeakerReviewWorkflow(
        gateway=gateway,  # type: ignore[arg-type]
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        final_review_model="gpt-5.6-sol",
        primary_reasoning_effort="low",
        adjudication_reasoning_effort="medium",
        final_review_reasoning_effort="high",
        expected_next_adjudication_request_sha256=expected_request_sha256,
    )


def _prepared_state(*, parts: int) -> SpeakerReviewRunState:
    return SpeakerReviewRunState(
        schema_version=5,
        run_id="speaker-review-0123456789abcdef",
        status=SpeakerReviewRunStatus.ADJUDICATION_PREPARED,
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
        adjudication_part_count=parts,
    )


def _completed_checkpoint(
    run: Path,
    *,
    completed: int = 1,
    parts: int = 3,
) -> SpeakerReviewRunState:
    for part in range(1, parts + 1):
        (run / f"adjudication-part-{part:04d}-requests.jsonl").write_bytes(
            f'{{"custom_id":"part-{part}"}}\n'.encode()
        )
    setup_gateway = SubmissionGateway()
    operation = _workflow(setup_gateway)
    state = operation.submit_first_adjudication(run, _prepared_state(parts=parts))
    (run / "adjudication-part-0001-output.jsonl").write_bytes(b'{"part":1}\n')
    state = replace(
        state,
        status=SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED,
        adjudication_completed_part_count=1,
    )
    for part in range(2, completed + 1):
        state = operation.submit_next_adjudication_part(run, state)
        (run / f"adjudication-part-{part:04d}-output.jsonl").write_bytes(
            f'{{"part":{part}}}\n'.encode()
        )
        state = replace(
            state,
            status=SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED,
            adjudication_completed_part_count=part,
        )
    return state


def test_submits_exactly_part_two_and_does_not_process_results(tmp_path: Path) -> None:
    state = _completed_checkpoint(tmp_path)
    gateway = SubmissionGateway()

    updated = _workflow(gateway).submit_next_adjudication_part(tmp_path, state)

    assert gateway.parts == [2]
    assert updated.status is SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED
    assert updated.adjudication_completed_part_count == 1
    assert updated.adjudication_batch_ids == ("batch-1", "batch-2")
    assert updated.adjudication_input_file_ids == ("file-1", "file-2")
    assert load_run_state(tmp_path / "run-state.json") == updated
    assert not (tmp_path / "adjudication-verdicts.jsonl").exists()
    assert not (tmp_path / "final-decisions.jsonl").exists()


def test_generic_transition_submits_part_k_plus_one(tmp_path: Path) -> None:
    state = _completed_checkpoint(tmp_path, completed=2, parts=4)
    gateway = SubmissionGateway()

    updated = _workflow(gateway).submit_next_adjudication_part(tmp_path, state)

    assert gateway.parts == [3]
    assert updated.adjudication_completed_part_count == 2
    assert updated.adjudication_batch_ids == ("batch-1", "batch-2", "batch-3")
    assert updated.adjudication_input_file_ids == ("file-1", "file-2", "file-3")


def test_submitting_final_part_still_stops_before_processing(tmp_path: Path) -> None:
    state = _completed_checkpoint(tmp_path, completed=2, parts=3)

    updated = _workflow(SubmissionGateway()).submit_next_adjudication_part(
        tmp_path, state
    )

    assert updated.status is SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED
    assert updated.adjudication_completed_part_count == 2
    assert not (tmp_path / "adjudication-verdicts.jsonl").exists()
    assert not (tmp_path / "final-decisions.jsonl").exists()


def test_exact_submitted_replay_never_calls_provider(tmp_path: Path) -> None:
    state = _completed_checkpoint(tmp_path)
    submitted = _workflow(SubmissionGateway()).submit_next_adjudication_part(
        tmp_path, state
    )
    disabled = SubmissionGateway(fail=True)

    assert (
        _workflow(disabled).submit_next_adjudication_part(tmp_path, submitted)
        is submitted
    )
    assert disabled.parts == []


def test_completed_journal_repairs_state_after_state_write_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _completed_checkpoint(tmp_path)
    gateway = SubmissionGateway()
    operation = _workflow(gateway)
    original_save = workflow_module.save_run_state

    monkeypatch.setattr(
        workflow_module,
        "save_run_state",
        lambda *_: (_ for _ in ()).throw(OSError("simulated state crash")),
    )
    with pytest.raises(OSError, match="state crash"):
        operation.submit_next_adjudication_part(tmp_path, state)
    monkeypatch.setattr(workflow_module, "save_run_state", original_save)

    recovered = operation.submit_next_adjudication_part(tmp_path, state)

    assert gateway.parts == [2]
    assert recovered.status is SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED
    assert recovered.adjudication_batch_ids[-1] == "batch-2"


def test_intent_only_checkpoint_never_resubmits(tmp_path: Path) -> None:
    state = _completed_checkpoint(tmp_path)
    gateway = SubmissionGateway(fail=True)
    operation = _workflow(gateway)

    with pytest.raises(RuntimeError, match="operator reconciliation"):
        operation.submit_next_adjudication_part(tmp_path, state)
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        operation.submit_next_adjudication_part(tmp_path, state)

    assert gateway.parts == [2]


def test_expected_request_hash_is_checked_at_provider_boundary(tmp_path: Path) -> None:
    state = _completed_checkpoint(tmp_path)
    expected = sha256(b'{"custom_id":"part-2"}\n').hexdigest()
    (tmp_path / "adjudication-part-0002-requests.jsonl").write_bytes(
        b'{"custom_id":"tampered"}\n'
    )
    gateway = SubmissionGateway()

    with pytest.raises(RuntimeError, match="completed-part checkpoint"):
        _workflow(
            gateway,
            expected_request_sha256=expected,
        ).submit_next_adjudication_part(tmp_path, state)

    assert gateway.parts == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda state: replace(state, adjudication_completed_part_count=0),
        lambda state: replace(state, adjudication_completed_part_count=3),
        lambda state: replace(state, adjudication_batch_id="other"),
        lambda state: replace(
            state, adjudication_batch_ids=("batch-1", "batch-1")
        ),
    ],
)
def test_invalid_checkpoint_shapes_fail_before_provider(
    tmp_path: Path,
    mutation: object,
) -> None:
    state = _completed_checkpoint(tmp_path)
    gateway = SubmissionGateway()

    with pytest.raises(RuntimeError, match="completed-part checkpoint"):
        _workflow(gateway).submit_next_adjudication_part(
            tmp_path,
            mutation(state),  # type: ignore[operator]
        )
    assert gateway.parts == []


def test_replay_rejects_active_part_output_as_rollback_evidence(tmp_path: Path) -> None:
    state = _completed_checkpoint(tmp_path)
    submitted = _workflow(SubmissionGateway()).submit_next_adjudication_part(
        tmp_path, state
    )
    (tmp_path / "adjudication-part-0002-output.jsonl").write_bytes(b"unexpected\n")

    with pytest.raises(RuntimeError, match="completed-part checkpoint"):
        _workflow(SubmissionGateway(fail=True)).submit_next_adjudication_part(
            tmp_path, submitted
        )
