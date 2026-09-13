from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from scripts import observe_first_private_speaker_review_adjudication_workspace as worker
from scripts import private_speaker_review_first_adjudication_observation_contract as contract

from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review.workflow import SpeakerReviewRunState

RUN_ID = "speaker-review-0123456789abcdef"
AUTHORIZATION_ID = "123e4567-e89b-42d3-a456-426614174000"


def _state(
    status: SpeakerReviewRunStatus = SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED,
) -> SpeakerReviewRunState:
    completed = 1 if status is SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED else 0
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
        primary_batch_ids=("primary-batch",),
        primary_input_file_ids=("primary-file",),
        adjudication_part_count=1,
        adjudication_completed_part_count=completed,
        adjudication_batch_id="adjudication-batch",
        adjudication_input_file_id="adjudication-file",
        adjudication_batch_ids=("adjudication-batch",),
        adjudication_input_file_ids=("adjudication-file",),
    )


def _environment(*, checkpoint: bool = True) -> dict[str, str]:
    result = {
        contract.ENV_ARCHIVE_SHA256: "a" * 64,
        contract.ENV_AUTHORIZATION_ID: AUTHORIZATION_ID,
        contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: "5000000",
        contract.ENV_RUN_ID: RUN_ID,
    }
    if checkpoint:
        result.update(
            {
                contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: "b" * 64,
                contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: "c" * 64,
                contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: "d" * 64,
                contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256: "e" * 64,
                contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256: "f" * 64,
                contract.ENV_EXPECTED_REQUEST_SHA256: "1" * 64,
            }
        )
    return result


def _mock_loaded_run(
    monkeypatch: pytest.MonkeyPatch,
    run: Path,
    state: SpeakerReviewRunState,
) -> None:
    monkeypatch.setattr(worker, "_run_directory", lambda *_args: run)
    monkeypatch.setattr(worker, "load_validated_run_state", lambda *_args: (run, state))
    monkeypatch.setattr(
        worker,
        "_estimated_adjudication_cost_microusd",
        lambda *_args: 250_000,
    )


def test_submitted_run_requires_all_root_digest_bindings_before_secret_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_loaded_run(monkeypatch, tmp_path, _state())
    monkeypatch.setattr(
        worker,
        "read_stable_openai_secret",
        lambda *_args: pytest.fail("secret must remain unread"),
    )

    with pytest.raises(worker.FirstAdjudicationObservationWorkerError, match="checkpoint"):
        worker.observe_first_adjudication(
            environment=_environment(checkpoint=False),
            review_root=tmp_path,
        )


def test_observation_calls_only_the_narrow_graph_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _state()
    after = replace(
        before,
        status=SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED,
        updated_at="2026-01-01T00:01:00+00:00",
        adjudication_completed_part_count=1,
    )
    _mock_loaded_run(monkeypatch, tmp_path, before)
    checked: list[bool] = []
    monkeypatch.setattr(
        worker,
        "_validate_expected_checkpoint",
        lambda *_args: checked.append(True),
    )
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda *_args: "secret")

    class _Graph:
        def observe_first_adjudication(
            self,
            run: Path,
            *,
            verified_run_state: SpeakerReviewRunState,
        ) -> tuple[Path, SpeakerReviewRunState]:
            assert verified_run_state is before
            return run, after

    monkeypatch.setattr(worker, "_workflow", lambda secret: _Graph())

    result = worker.observe_first_adjudication(
        environment=_environment(),
        review_root=tmp_path,
    )

    assert checked == [True]
    assert result["status"] == "observed"
    assert result["run_status"] == "adjudication_part_completed"
    assert set(result) == contract.AGGREGATE_KEYS
    assert not any("batch" in key or "file" in key or "provider" in key for key in result)


def test_observed_replay_physically_reads_inventory_without_secret_or_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state(SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED)
    _mock_loaded_run(monkeypatch, tmp_path, state)
    required, _optional = worker._expected_inventory_names(state, observed=True)
    for name in required:
        path = tmp_path / name
        path.write_bytes(b"evidence\n")
        path.chmod(0o600)
    reads: list[str] = []
    original_read = worker._read_checkpoint_file

    def read(path: Path, maximum: int) -> bytes:
        reads.append(path.name)
        return original_read(path, maximum)

    monkeypatch.setattr(worker, "_read_checkpoint_file", read)
    monkeypatch.setattr(
        worker,
        "read_stable_openai_secret",
        lambda *_args: pytest.fail("secret must remain unread"),
    )
    monkeypatch.setattr(worker, "_workflow", lambda *_args: pytest.fail("provider"))

    result = worker.observe_first_adjudication(
        environment=_environment(checkpoint=False),
        review_root=tmp_path,
    )

    assert result["status"] == "already_observed"
    assert set(reads) == required


def test_worker_rejects_state_mutation_outside_the_observation_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _state()
    after = replace(
        before,
        status=SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED,
        updated_at="2026-01-01T00:01:00+00:00",
        adjudication_completed_part_count=1,
        candidate_count=3,
    )
    _mock_loaded_run(monkeypatch, tmp_path, before)
    monkeypatch.setattr(worker, "_validate_expected_checkpoint", lambda *_args: None)
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda *_args: "secret")

    class _Graph:
        def observe_first_adjudication(self, run: Path, **_kwargs: object):
            return run, after

    monkeypatch.setattr(worker, "_workflow", lambda _secret: _Graph())

    with pytest.raises(worker.FirstAdjudicationObservationWorkerError, match="result"):
        worker.observe_first_adjudication(environment=_environment(), review_root=tmp_path)
