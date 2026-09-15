from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from scripts import private_speaker_review_third_adjudication_submission_contract as contract
from scripts import submit_third_private_speaker_review_adjudication_workspace as worker

from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review.workflow import SpeakerReviewRunState

RUN_ID = "speaker-review-0123456789abcdef"


def _state(
    status: SpeakerReviewRunStatus = SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED,
    *,
    completed: int = 2,
) -> SpeakerReviewRunState:
    submitted = status is SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED
    ids = tuple(f"batch-{part}" for part in range(1, completed + 1))
    inputs = tuple(f"file-{part}" for part in range(1, completed + 1))
    if submitted:
        ids = (*ids, f"batch-{completed + 1}")
        inputs = (*inputs, f"file-{completed + 1}")
    return SpeakerReviewRunState(
        schema_version=5,
        run_id=RUN_ID,
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
        adjudication_part_count=3,
        adjudication_completed_part_count=completed,
        adjudication_batch_id=ids[-1],
        adjudication_input_file_id=inputs[-1],
        adjudication_batch_ids=ids,
        adjudication_input_file_ids=inputs,
    )


def _environment() -> dict[str, str]:
    return {
        contract.ENV_ARCHIVE_SHA256: "a" * 64,
        contract.ENV_AUTHORIZATION_ID: "123e4567-e89b-42d3-a456-426614174000",
        contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: "5000000",
        contract.ENV_RUN_ID: RUN_ID,
    }


def _patch_preflight(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    state: SpeakerReviewRunState,
    contents: dict[str, bytes] | None = None,
) -> dict[str, bytes]:
    snapshot = (
        {
            "run-state.json": b"state\n",
            "adjudication-part-0003-requests.jsonl": b'{"body":{}}\n',
        }
        if contents is None
        else contents
    )
    monkeypatch.setattr(worker, "_run_directory", lambda *_: tmp_path)
    monkeypatch.setattr(worker, "_inventory", lambda *_: (snapshot, state))
    monkeypatch.setattr(worker, "_digest_bindings", lambda *_: None)
    monkeypatch.setattr(worker, "_expected_request_hash", lambda *_: "b" * 64)
    monkeypatch.setattr(worker, "_validate_checkpoint_shape", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker, "_validate_completed_parts", lambda *_: None)
    monkeypatch.setattr(worker, "_validate_replay_evidence", lambda *_: None)
    monkeypatch.setattr(worker, "_parse_requests", lambda *_: ({"body": {}},))
    monkeypatch.setattr(worker, "estimate_batch_cost_usd", lambda **_: 0.001)
    return snapshot


def test_fresh_part_three_submission_reads_secret_only_after_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    _patch_preflight(monkeypatch, tmp_path, state)
    events: list[str] = []
    updated = replace(
        state,
        status=SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED,
        adjudication_batch_id="batch-3",
        adjudication_input_file_id="file-3",
        adjudication_batch_ids=("batch-1", "batch-2", "batch-3"),
        adjudication_input_file_ids=("file-1", "file-2", "file-3"),
    )

    class Graph:
        def submit_next_adjudication(
            self,
            run: Path,
            *,
            verified_run_state: SpeakerReviewRunState,
        ) -> tuple[Path, SpeakerReviewRunState]:
            events.append("provider")
            assert verified_run_state is state
            return run, updated

    monkeypatch.setattr(
        worker,
        "read_stable_openai_secret",
        lambda *_: events.append("secret") or "secret",
    )
    monkeypatch.setattr(worker, "_workflow", lambda *_args, **_kwargs: Graph())

    result = worker.submit_third_adjudication(
        environment=_environment(), review_root=tmp_path
    )

    assert events == ["secret", "provider"]
    assert result["status"] == "submitted"
    assert result["run_status"] == "adjudication_submitted"
    assert result["adjudication_completed_part_count"] == 2


def test_submitted_replay_never_reads_secret_or_constructs_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state(SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED)
    _patch_preflight(monkeypatch, tmp_path, state)
    monkeypatch.setattr(
        worker,
        "read_stable_openai_secret",
        lambda *_: pytest.fail("secret must remain closed"),
    )
    monkeypatch.setattr(worker, "_workflow", lambda *_args, **_kwargs: pytest.fail("provider"))

    class Replay:
        def submit_next_adjudication(
            self,
            run: Path,
            *,
            verified_run_state: SpeakerReviewRunState,
        ) -> tuple[Path, SpeakerReviewRunState]:
            return run, verified_run_state

    monkeypatch.setattr(worker, "_replay_workflow", lambda **_: Replay())

    result = worker.submit_third_adjudication(
        environment=_environment(), review_root=tmp_path
    )

    assert result["status"] == "already_submitted"


@pytest.mark.parametrize(
    "journal_names",
    [
        {".adjudication-part-0003-submission-intent.json"},
        {".adjudication-part-0003-submission-completed.json"},
    ],
)
def test_partial_part_three_journal_requires_reconciliation_without_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    journal_names: set[str],
) -> None:
    state = _state()
    contents = {
        "run-state.json": b"state\n",
        "adjudication-part-0003-requests.jsonl": b'{"body":{}}\n',
        **{name: b"journal\n" for name in journal_names},
    }
    _patch_preflight(monkeypatch, tmp_path, state, contents)
    monkeypatch.setattr(
        worker,
        "read_stable_openai_secret",
        lambda *_: pytest.fail("secret must remain closed"),
    )

    result = worker.submit_third_adjudication(
        environment=_environment(), review_root=tmp_path
    )

    assert result["status"] == "reconciliation_required"
    assert result["submitted_part_count"] == 0


def test_matching_part_three_journal_recovers_without_secret_or_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    contents = {
        "run-state.json": b"state\n",
        "adjudication-part-0003-requests.jsonl": b'{"body":{}}\n',
        ".adjudication-part-0003-submission-intent.json": b"intent\n",
        ".adjudication-part-0003-submission-completed.json": b"completed\n",
    }
    _patch_preflight(monkeypatch, tmp_path, state, contents)
    monkeypatch.setattr(
        worker,
        "read_stable_openai_secret",
        lambda *_: pytest.fail("secret must remain closed"),
    )
    recovered = replace(
        state,
        status=SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED,
        adjudication_batch_id="batch-3",
        adjudication_input_file_id="file-3",
        adjudication_batch_ids=("batch-1", "batch-2", "batch-3"),
        adjudication_input_file_ids=("file-1", "file-2", "file-3"),
    )

    class Replay:
        def submit_next_adjudication(
            self,
            run: Path,
            *,
            verified_run_state: SpeakerReviewRunState,
        ) -> tuple[Path, SpeakerReviewRunState]:
            return run, recovered

    monkeypatch.setattr(worker, "_replay_workflow", lambda **_: Replay())

    result = worker.submit_third_adjudication(
        environment=_environment(), review_root=tmp_path
    )

    assert result["status"] == "submitted"


@pytest.mark.parametrize(
    "active_name",
    [
        "adjudication-part-0003-output.jsonl",
        "adjudication-part-0003-api-errors.jsonl",
    ],
)
def test_active_part_three_output_is_rejected_before_secret_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    active_name: str,
) -> None:
    state = _state()
    contents = {
        "run-state.json": b"state\n",
        "adjudication-part-0003-requests.jsonl": b'{"body":{}}\n',
        active_name: b"unexpected\n",
    }
    _patch_preflight(monkeypatch, tmp_path, state, contents)
    monkeypatch.setattr(
        worker,
        "read_stable_openai_secret",
        lambda *_: pytest.fail("secret must remain closed"),
    )

    with pytest.raises(worker.ThirdAdjudicationSubmissionWorkerError):
        worker.submit_third_adjudication(
            environment=_environment(), review_root=tmp_path
        )


def test_wrong_completed_count_stops_before_secret_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state(completed=1)
    _patch_preflight(monkeypatch, tmp_path, state)
    monkeypatch.setattr(
        worker,
        "read_stable_openai_secret",
        lambda *_: pytest.fail("secret must remain closed"),
    )

    with pytest.raises(
        worker.ThirdAdjudicationSubmissionWorkerError, match="predecessor"
    ):
        worker.submit_third_adjudication(
            environment=_environment(), review_root=tmp_path
        )


def test_digest_mismatch_stops_before_secret_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    monkeypatch.setattr(worker, "_run_directory", lambda *_: tmp_path)
    monkeypatch.setattr(
        worker, "_inventory", lambda *_: ({"run-state.json": b"state"}, state)
    )
    monkeypatch.setattr(
        worker,
        "_digest_bindings",
        lambda *_: (_ for _ in ()).throw(
            worker.ThirdAdjudicationSubmissionWorkerError("changed")
        ),
    )
    monkeypatch.setattr(
        worker,
        "read_stable_openai_secret",
        lambda *_: pytest.fail("secret must remain closed"),
    )

    with pytest.raises(worker.ThirdAdjudicationSubmissionWorkerError, match="changed"):
        worker.submit_third_adjudication(
            environment=_environment(), review_root=tmp_path
        )
