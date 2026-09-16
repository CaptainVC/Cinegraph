from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from scripts import observe_third_private_speaker_review_adjudication_workspace as worker
from scripts import private_speaker_review_third_adjudication_observation_contract as contract

from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review.workflow import SpeakerReviewRunState

RUN_ID = "speaker-review-0123456789abcdef"
AUTHORIZATION_ID = "123e4567-e89b-42d3-a456-426614174000"
DIGEST = "a" * 64


def _state(
    status: SpeakerReviewRunStatus = SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED,
    completed: int = 2,
) -> SpeakerReviewRunState:
    ids = tuple(f"batch-{part}" for part in range(1, completed + 2))
    input_ids = tuple(f"file-{part}" for part in range(1, completed + 2))
    if status is SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED:
        ids, input_ids = ids[:completed], input_ids[:completed]
    return SpeakerReviewRunState(
        schema_version=5,
        run_id=RUN_ID,
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
        adjudication_part_count=3,
        adjudication_completed_part_count=completed,
        adjudication_batch_id=ids[-1],
        adjudication_input_file_id=input_ids[-1],
        adjudication_batch_ids=ids,
        adjudication_input_file_ids=input_ids,
    )


def _environment(**overrides: str) -> dict[str, str]:
    values = {
        contract.ENV_ARCHIVE_SHA256: DIGEST,
        contract.ENV_AUTHORIZATION_ID: AUTHORIZATION_ID,
        contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: "5000000",
        contract.ENV_RUN_ID: RUN_ID,
    }
    values.update(overrides)
    return values


def test_request_from_environment_is_strict_and_rejects_missing_or_noncanonical_cap() -> None:
    request = worker.request_from_environment(_environment())
    assert request["operation"] == contract.OPERATION
    assert request["season_number"] == contract.SEASON_NUMBER
    with pytest.raises(worker.ThirdAdjudicationObservationWorkerError):
        worker.request_from_environment(_environment(**{contract.ENV_RUN_ID: "bad"}))
    with pytest.raises(worker.ThirdAdjudicationObservationWorkerError):
        worker.request_from_environment(
            _environment(**{contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: "05000000"})
        )


def test_worker_args_bind_all_six_checkpoint_digests(tmp_path: Path) -> None:
    request = worker.request_from_environment(_environment())
    binding = {
        "pre_run_state_sha256": "1" * 64,
        "pre_artifact_set_sha256": "2" * 64,
        "pre_journal_set_sha256": "3" * 64,
        "pre_output_set_sha256": "4" * 64,
        "pre_derived_set_sha256": "5" * 64,
        "request_sha256": "6" * 64,
    }
    # The root coordinator owns this argument shape; the worker's environment
    # parser must accept the exact request that accompanies it.
    assert request["run_id"] == RUN_ID
    assert set(binding) == {
        "pre_run_state_sha256",
        "pre_artifact_set_sha256",
        "pre_journal_set_sha256",
        "pre_output_set_sha256",
        "pre_derived_set_sha256",
        "request_sha256",
    }
    with pytest.raises(worker.ThirdAdjudicationObservationWorkerError):
        worker._checkpoint_binding_present({contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: "1"})
    assert worker._checkpoint_binding_present(
        {
            contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: "1" * 64,
            contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: "2" * 64,
            contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: "3" * 64,
            contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256: "4" * 64,
            contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256: "5" * 64,
            contract.ENV_EXPECTED_REQUEST_SHA256: "6" * 64,
        }
    )
    assert not worker._checkpoint_binding_present({})


@pytest.mark.parametrize("replay", [False, True])
def test_partial_checkpoint_digest_rejects_before_secret_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replay: bool
) -> None:
    run = tmp_path / RUN_ID
    run.mkdir()
    state = (
        _state(SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED, completed=3)
        if replay
        else _state()
    )
    monkeypatch.setattr(worker, "_run_directory", lambda *_: run)
    monkeypatch.setattr(worker, "load_validated_run_state", lambda *_: (run, state))
    monkeypatch.setattr(worker, "_estimated_adjudication_cost_microusd", lambda *_: 100_000)
    monkeypatch.setattr(
        worker, "read_stable_openai_secret", lambda *_: pytest.fail("secret must not be read")
    )
    environment = _environment(**{contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: "1" * 64})
    with pytest.raises(worker.ThirdAdjudicationObservationWorkerError, match="checkpoint"):
        worker.observe_third_adjudication(environment=environment, review_root=tmp_path)


def test_cost_cap_rejects_before_secret_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = tmp_path / RUN_ID
    run.mkdir()
    state = _state()
    monkeypatch.setattr(worker, "_run_directory", lambda *_: run)
    monkeypatch.setattr(worker, "load_validated_run_state", lambda *_: (run, state))
    monkeypatch.setattr(worker, "_validate_expected_checkpoint", lambda *_: None)
    monkeypatch.setattr(worker, "_estimated_adjudication_cost_microusd", lambda *_: 5_000_000)
    monkeypatch.setattr(
        worker, "read_stable_openai_secret", lambda *_: pytest.fail("secret must not be read")
    )
    with pytest.raises(worker.ThirdAdjudicationObservationWorkerError, match="cost exceeds"):
        worker.observe_third_adjudication(environment=_environment(), review_root=tmp_path)


def test_exactly_three_part_checkpoint_is_accepted_before_secret_access(
    tmp_path: Path,
) -> None:
    state = _state()
    required, _ = worker._expected_inventory_names(state, observed=False)
    contents: dict[str, bytes] = {}
    for name in required:
        raw = f"{name}\n".encode("ascii")
        (tmp_path / name).write_bytes(raw)
        contents[name] = raw

    artifacts: dict[str, bytes] = {}
    journals: dict[str, bytes] = {}
    outputs: dict[str, bytes] = {}
    derived: dict[str, bytes] = {}
    for name, raw in contents.items():
        if name == "run-state.json":
            continue
        if name.startswith("."):
            journals[name] = raw
        elif name.endswith("-output.jsonl") or name.endswith("-api-errors.jsonl"):
            outputs[name] = raw
        elif name.endswith("-requests.jsonl") or name in {
            "candidates.jsonl",
            "source-manifest.json",
        }:
            artifacts[name] = raw
        else:
            derived[name] = raw

    environment = {
        contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: hashlib.sha256(
            contents["run-state.json"]
        ).hexdigest(),
        contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: worker._set_digest(artifacts),
        contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: worker._set_digest(journals),
        contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256: worker._set_digest(outputs),
        contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256: worker._set_digest(derived),
        contract.ENV_EXPECTED_REQUEST_SHA256: hashlib.sha256(
            contents["adjudication-part-0003-requests.jsonl"]
        ).hexdigest(),
    }

    worker._validate_expected_checkpoint(tmp_path, state, environment)


def test_aggregate_is_exact_and_transition_rejects_immutable_or_wrong_counts() -> None:
    state = _state()
    result = worker._aggregate(
        state, status="waiting", estimated_adjudication_cost_microusd=500_000
    )
    assert set(result) == contract.AGGREGATE_KEYS
    assert result["actual_primary_cost_microusd"] == 100_000
    assert result["adjudication_completed_part_count"] == 2
    completed = replace(
        state,
        status=SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED,
        adjudication_completed_part_count=3,
    )
    worker._validate_state_transition(state, completed, Path("run"), Path("run"))
    with pytest.raises(worker.ThirdAdjudicationObservationWorkerError):
        worker._validate_state_transition(
            state, replace(completed, adjudication_part_count=4), Path("run"), Path("run")
        )
    with pytest.raises(worker.ThirdAdjudicationObservationWorkerError):
        worker._validate_state_transition(
            state,
            replace(completed, run_id="speaker-review-fedcba9876543210"),
            Path("run"),
            Path("run"),
        )


@pytest.mark.skipif(
    __import__("os").name == "nt", reason="symlinks require elevated Windows privileges"
)
def test_secret_reader_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("sk-private", encoding="utf-8")
    link = tmp_path / "openai_api_key"
    link.symlink_to(target)
    with pytest.raises(worker.ThirdAdjudicationObservationWorkerError):
        worker.read_stable_openai_secret(link)
