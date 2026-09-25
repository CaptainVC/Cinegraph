from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import (
    private_speaker_review_next_final_review_submission_contract as contract,
)
from scripts import submit_next_final_private_speaker_review_workspace as worker

from cinegraph.domain.enums.enum import SpeakerReviewRunStatus


def _environment() -> dict[str, str]:
    return {
        contract.ENV_ARCHIVE_SHA256: "a" * 64,
        contract.ENV_AUTHORIZATION_ID: "12345678-1234-4234-8234-123456789abc",
        contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: "5000000",
        contract.ENV_RUN_ID: "speaker-review-0123456789abcdef",
        contract.ENV_EXPECTED_REQUEST_SHA256: "b" * 64,
    }


def _state(*, completed_ids: bool = False, one_part: bool = False) -> SimpleNamespace:
    count = 1 if one_part else 2
    ids = ("batch-1", "batch-2") if completed_ids else ("batch-1",)
    inputs = ("input-1", "input-2") if completed_ids else ("input-1",)
    return SimpleNamespace(
        status=SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED,
        final_review_part_count=count,
        final_review_completed_part_count=1,
        final_review_batch_ids=ids,
        final_review_input_file_ids=inputs,
        actual_primary_cost_usd=0.0,
        actual_adjudication_cost_usd=0.0,
        actual_final_review_cost_usd=0.0,
        maximum_cost_usd=5.0,
        run_id="speaker-review-0123456789abcdef",
    )


def test_success_submits_part_two_with_expected_request_digest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _state()
    request = b'{"custom_id":"x"}\n'
    updated = _state(completed_ids=True)
    calls: list[str] = []

    class Graph:
        def submit_next_final_review(self, run: Path, *, verified_run_state: object):
            assert verified_run_state is state
            calls.append("graph")
            return run, updated

    monkeypatch.setattr(worker, "_run_directory", lambda run_id, root: tmp_path)
    monkeypatch.setattr(worker, "_inventory", lambda run: ({"final-review-part-0002-requests.jsonl": request}, state))
    monkeypatch.setattr(worker, "_bind", lambda contents, environment: request)
    monkeypatch.setattr(worker, "estimate_batch_cost_usd", lambda **kwargs: 0.0)
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda path: calls.append("secret") or "key")
    monkeypatch.setattr(worker, "_workflow", lambda secret, expected_request_sha256, maximum_authorized_cost_usd: (calls.append(expected_request_sha256) or Graph()))

    result = worker.submit_next_final_review(environment=_environment(), secret_path=tmp_path / "secret")

    assert result["status"] == "submitted"
    assert calls[0] == "secret"
    assert calls[1] == worker._sha(request)
    assert calls[-1] == "graph"


def test_successful_replay_is_provider_and_secret_free(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _state(completed_ids=True)
    request = b'{"custom_id":"x"}\n'
    contents = {
        "final-review-part-0002-requests.jsonl": request,
        ".final-review-part-0002-submission-intent.json": b"intent",
        ".final-review-part-0002-submission-completed.json": b"completed",
    }
    calls: list[str] = []

    class Graph:
        def submit_next_final_review(self, run: Path, *, verified_run_state: object):
            calls.append("replay")
            return run, state

    monkeypatch.setattr(worker, "_run_directory", lambda run_id, root: tmp_path)
    monkeypatch.setattr(worker, "_inventory", lambda run: (contents, state))
    monkeypatch.setattr(worker, "_bind", lambda contents, environment: request)
    monkeypatch.setattr(worker, "estimate_batch_cost_usd", lambda **kwargs: 0.0)
    monkeypatch.setattr(worker, "_replay", lambda expected_request_sha256, maximum_authorized_cost_usd: Graph())
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda path: (_ for _ in ()).throw(AssertionError("secret read")))

    result = worker.submit_next_final_review(environment=_environment(), secret_path=tmp_path / "secret")

    assert result["status"] == "already_submitted"
    assert calls == ["replay"]


def test_wrong_preflight_digest_fails_before_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _state()
    contents = {
        "run-state.json": b"state",
        "final-review-part-0002-requests.jsonl": b'{"custom_id":"x"}\n',
    }
    environment = _environment()
    for key in (
        contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256,
        contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256,
        contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256,
        contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256,
        contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256,
    ):
        environment[key] = "0" * 64

    monkeypatch.setattr(worker, "_run_directory", lambda run_id, root: tmp_path)
    monkeypatch.setattr(worker, "_inventory", lambda run: (contents, state))
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda path: (_ for _ in ()).throw(AssertionError("secret read")))

    with pytest.raises(worker.NextFinalReviewSubmissionWorkerError):
        worker.submit_next_final_review(environment=environment, secret_path=tmp_path / "secret")


def test_one_part_terminal_path_does_not_bind_or_read_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _state(one_part=True)
    monkeypatch.setattr(worker, "_run_directory", lambda run_id, root: tmp_path)
    monkeypatch.setattr(worker, "_inventory", lambda run: ({}, state))
    monkeypatch.setattr(worker, "_bind", lambda *args: (_ for _ in ()).throw(AssertionError("bind")))
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda path: (_ for _ in ()).throw(AssertionError("secret read")))

    result = worker.submit_next_final_review(environment=_environment(), secret_path=tmp_path / "secret")

    assert result["status"] == "all_parts_completed"
