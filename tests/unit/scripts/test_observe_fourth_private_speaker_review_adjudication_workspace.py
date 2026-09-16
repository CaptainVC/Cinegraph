from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from pathlib import Path

import pytest
from scripts import observe_fourth_private_speaker_review_adjudication_workspace as worker
from scripts import private_speaker_review_fourth_adjudication_observation_contract as contract

from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review.workflow import SpeakerReviewRunState

RUN_ID = "speaker-review-0123456789abcdef"
AUTHORIZATION_ID = "123e4567-e89b-42d3-a456-426614174000"
DIGEST = "a" * 64


def _state(status: SpeakerReviewRunStatus = SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED, count: int = 3) -> SpeakerReviewRunState:
    return SpeakerReviewRunState(
        schema_version=5, run_id=RUN_ID, status=status,
        created_at="2026-09-16T00:00:00+00:00", updated_at="2026-09-16T00:00:01+00:00",
        candidate_count=2, primary_model="gpt-5.6-luna", adjudication_model="gpt-5.6-terra",
        prompt_version="speaker-review-v1", maximum_cost_usd=5.0,
        estimated_primary_cost_usd=0.25, actual_primary_cost_usd=0.10,
        actual_adjudication_cost_usd=0.0, primary_part_count=1, primary_completed_part_count=1,
        adjudication_part_count=4, adjudication_completed_part_count=count,
        adjudication_batch_id="batch-4", adjudication_input_file_id="file-4",
        adjudication_batch_ids=tuple(f"batch-{n}" for n in range(1, 5)),
        adjudication_input_file_ids=tuple(f"file-{n}" for n in range(1, 5)),
    )


def _environment(**overrides: str) -> dict[str, str]:
    value = {
        contract.ENV_ARCHIVE_SHA256: DIGEST,
        contract.ENV_AUTHORIZATION_ID: AUTHORIZATION_ID,
        contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: "5000000",
        contract.ENV_RUN_ID: RUN_ID,
    }
    value.update(overrides)
    return value


def test_request_environment_is_strict_and_canonical() -> None:
    request = worker.request_from_environment(_environment())
    assert request["operation"] == contract.OPERATION
    assert request["season_number"] == contract.SEASON_NUMBER
    for key, value in ((contract.ENV_RUN_ID, "bad"), (contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD, "05000000")):
        with pytest.raises(worker.FourthAdjudicationObservationWorkerError):
            worker.request_from_environment(_environment(**{key: value}))


def test_checkpoint_binding_is_all_or_none_and_validated() -> None:
    names = [
        contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256,
        contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256,
        contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256,
        contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256,
        contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256,
        contract.ENV_EXPECTED_REQUEST_SHA256,
    ]
    with pytest.raises(worker.FourthAdjudicationObservationWorkerError):
        worker._checkpoint_binding_present({names[0]: "1" * 64})
    assert not worker._checkpoint_binding_present({})
    assert worker._checkpoint_binding_present({name: "1" * 64 for name in names})
    with pytest.raises(worker.FourthAdjudicationObservationWorkerError):
        worker._checkpoint_binding_present({name: "z" * 64 for name in names})


@pytest.mark.parametrize("replay", [False, True])
def test_partial_checkpoint_rejects_before_secret_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replay: bool,
) -> None:
    run = tmp_path / RUN_ID
    run.mkdir()
    state = _state(SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED, 4) if replay else _state()
    monkeypatch.setattr(worker, "_run_directory", lambda *_: run)
    monkeypatch.setattr(worker, "load_validated_run_state", lambda *_: (run, state))
    monkeypatch.setattr(worker, "_estimated_adjudication_cost_microusd", lambda *_: 100_000)
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda *_: pytest.fail("secret read too early"))
    with pytest.raises(worker.FourthAdjudicationObservationWorkerError, match="checkpoint"):
        worker.observe_fourth_adjudication(
            environment=_environment(**{contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: "1" * 64}),
            review_root=tmp_path,
        )


def test_cost_cap_rejects_before_secret_access(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = tmp_path / RUN_ID
    run.mkdir()
    state = _state()
    monkeypatch.setattr(worker, "_run_directory", lambda *_: run)
    monkeypatch.setattr(worker, "load_validated_run_state", lambda *_: (run, state))
    monkeypatch.setattr(worker, "_validate_expected_checkpoint", lambda *_: None)
    monkeypatch.setattr(worker, "_estimated_adjudication_cost_microusd", lambda *_: 5_000_000)
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda *_: pytest.fail("secret read too early"))
    with pytest.raises(worker.FourthAdjudicationObservationWorkerError, match="cost exceeds"):
        worker.observe_fourth_adjudication(environment=_environment(), review_root=tmp_path)


def test_validate_expected_checkpoint_rejects_unexpected_inventory_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    required, optional = worker._expected_inventory_names(state, observed=False)
    contents = {name: (name + "\n").encode() for name in required}
    for name, raw in contents.items():
        path = tmp_path / name
        path.write_bytes(raw)
        path.chmod(0o600)
    groups = {"artifacts": {}, "journals": {}, "outputs": {}, "derived": {}}
    for name, raw in contents.items():
        if name == "run-state.json":
            continue
        group = "journals" if name.startswith(".") else "outputs" if name.endswith(("-output.jsonl", "-api-errors.jsonl")) else "artifacts" if name.endswith("-requests.jsonl") or name in {"candidates.jsonl", "source-manifest.json"} else "derived"
        groups[group][name] = raw
    environment = {
        contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: hashlib.sha256(contents["run-state.json"]).hexdigest(),
        contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: worker._set_digest(groups["artifacts"]),
        contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: worker._set_digest(groups["journals"]),
        contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256: worker._set_digest(groups["outputs"]),
        contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256: worker._set_digest(groups["derived"]),
        contract.ENV_EXPECTED_REQUEST_SHA256: hashlib.sha256(contents["adjudication-part-0004-requests.jsonl"]).hexdigest(),
    }
    worker._validate_expected_checkpoint(tmp_path, state, environment)
    unexpected = tmp_path / "unexpected.json"
    unexpected.write_bytes(b"x\n")
    unexpected.chmod(0o600)
    with pytest.raises(worker.FourthAdjudicationObservationWorkerError, match="changed"):
        worker._validate_expected_checkpoint(tmp_path, state, environment)


def test_aggregate_and_transition_allow_only_fourth_part_delta() -> None:
    before = _state()
    waiting = worker._aggregate(before, status="waiting", estimated_adjudication_cost_microusd=1)
    assert set(waiting) == contract.AGGREGATE_KEYS
    observed = replace(before, status=SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED, adjudication_completed_part_count=4)
    worker._validate_state_transition(before, observed, Path("run"), Path("run"))
    failed = replace(before, status=SpeakerReviewRunStatus.FAILED)
    worker._validate_state_transition(before, failed, Path("run"), Path("run"))
    for invalid in (
        replace(observed, adjudication_completed_part_count=5),
        replace(observed, adjudication_part_count=5),
        replace(observed, run_id="speaker-review-fedcba9876543210"),
    ):
        with pytest.raises(worker.FourthAdjudicationObservationWorkerError):
            worker._validate_state_transition(before, invalid, Path("run"), Path("run"))


@pytest.mark.skipif(os.name != "posix", reason="secret ownership checks are POSIX-specific")
def test_secret_reader_is_bounded_and_rejects_links(tmp_path: Path) -> None:
    secret = tmp_path / "secret"
    secret.write_text("sk-test\n", encoding="utf-8")
    secret.chmod(0o600)
    assert worker.read_stable_openai_secret(secret) == "sk-test"
    target = tmp_path / "target"
    target.write_text("sk-other", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(worker.FourthAdjudicationObservationWorkerError):
        worker.read_stable_openai_secret(link)
    secret.write_bytes(b"x" * (worker.SECRET_MAX_BYTES + 1))
    with pytest.raises(worker.FourthAdjudicationObservationWorkerError):
        worker.read_stable_openai_secret(secret)
