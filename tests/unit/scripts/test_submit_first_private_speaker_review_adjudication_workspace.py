from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest
from scripts import private_speaker_review_first_adjudication_submission_contract as contract
from scripts import submit_first_private_speaker_review_adjudication_workspace as worker

from cinegraph.config import DEFAULT_SPEAKER_REVIEW_CONFIGURATION
from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review.workflow import SpeakerReviewRunState

RUN_ID = "speaker-review-0123456789abcdef"


def _state() -> SpeakerReviewRunState:
    return SpeakerReviewRunState(
        schema_version=5,
        run_id=RUN_ID,
        status=SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED,
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
        adjudication_part_count=2,
        adjudication_batch_id="private-batch",
        adjudication_input_file_id="private-file",
        adjudication_batch_ids=("private-batch",),
        adjudication_input_file_ids=("private-file",),
    )


def test_aggregate_is_small_and_contains_no_provider_identifiers() -> None:
    result = worker._aggregate(_state(), status="already_submitted", estimated=100, submitted=1)
    assert set(result) == contract.AGGREGATE_KEYS
    assert not any("batch" in key or "file" in key or "provider" in key for key in result)


def test_request_environment_requires_all_digest_bindings_before_run() -> None:
    environment = {
        contract.ENV_ARCHIVE_SHA256: "a" * 64,
        contract.ENV_AUTHORIZATION_ID: "123e4567-e89b-42d3-a456-426614174000",
        contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: "5000000",
        contract.ENV_RUN_ID: RUN_ID,
    }
    with pytest.raises(worker.FirstAdjudicationSubmissionWorkerError):
        worker.submit_first_adjudication(environment=environment, review_root=Path("."))


def test_pre_digest_classes_keep_adjudication_requests_in_derived() -> None:
    contents = {
        "run-state.json": b"state\n",
        "candidates.jsonl": b"candidate\n",
        "source-manifest.json": b"manifest\n",
        "primary-part-0001-requests.jsonl": b"primary\n",
        "adjudication-part-0001-requests.jsonl": b"adjudication\n",
        "primary-part-0001-output.jsonl": b"output\n",
        "primary-verdicts.jsonl": b"verdict\n",
    }
    artifacts = {
        name: contents[name]
        for name in (
            "candidates.jsonl",
            "source-manifest.json",
            "primary-part-0001-requests.jsonl",
        )
    }
    outputs = {"primary-part-0001-output.jsonl": contents["primary-part-0001-output.jsonl"]}
    derived = {
        "adjudication-part-0001-requests.jsonl": contents["adjudication-part-0001-requests.jsonl"],
        "primary-verdicts.jsonl": contents["primary-verdicts.jsonl"],
    }
    environment = {
        contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: worker._sha(contents["run-state.json"]),
        contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: worker._set_digest(artifacts),
        contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: worker._set_digest({}),
        contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256: worker._set_digest(outputs),
        contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256: worker._set_digest(derived),
    }
    worker._digest_bindings(contents, environment)


def test_replay_journals_use_strict_application_parser(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    run.chmod(0o700)
    request = b'{"body":{"max_output_tokens":1},"custom_id":"one"}\n'
    (run / "adjudication-part-0001-requests.jsonl").write_bytes(request)
    binding = {
        "batch_endpoint": DEFAULT_SPEAKER_REVIEW_CONFIGURATION.batch_endpoint,
        "completion_window": DEFAULT_SPEAKER_REVIEW_CONFIGURATION.batch_completion_window,
        "part": 1,
        "prompt_version": _state().prompt_version,
        "request_sha256": sha256(request).hexdigest(),
        "run_id": RUN_ID,
        "schema_version": 1,
        "stage": "adjudication",
    }
    intent = {"binding": binding, "status": "intent"}
    completed = {
        "batch_id": "private-batch",
        "binding": binding,
        "input_file_id": "private-file",
        "status": "validating",
    }
    (run / ".adjudication-part-0001-submission-intent.json").write_bytes(
        (json.dumps(intent, sort_keys=True, separators=(",", ":")) + "\n").encode()
    )
    (run / ".adjudication-part-0001-submission-completed.json").write_bytes(
        (json.dumps(completed, sort_keys=True, separators=(",", ":")) + "\n").encode()
    )
    contents = {path.name: path.read_bytes() for path in run.iterdir()}
    worker._validate_replay_evidence(run, contents, _state())
    for malformed in (
        json.dumps({**intent, "extra": True}, sort_keys=True, separators=(",", ":")) + "\n",
        '{"binding":'
        + json.dumps(binding, sort_keys=True, separators=(",", ":"))
        + ',"binding":'
        + json.dumps(binding, sort_keys=True, separators=(",", ":"))
        + ',"status":"intent"}\n',
    ):
        (run / ".adjudication-part-0001-submission-intent.json").write_bytes(malformed.encode())
        with pytest.raises(worker.FirstAdjudicationSubmissionWorkerError):
            worker._validate_replay_evidence(
                run,
                {name: path.read_bytes() for name, path in ((p.name, p) for p in run.iterdir())},
                _state(),
            )


def test_submitted_replay_never_reads_secret_or_constructs_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    submitted = _state()
    request = b'{"body":{"max_output_tokens":1}}\n'
    run_state = b"state"
    monkeypatch.setattr(worker, "_run_directory", lambda *_: tmp_path)
    monkeypatch.setattr(
        worker,
        "_inventory",
        lambda *_: (
            {"run-state.json": run_state, "adjudication-part-0001-requests.jsonl": request},
            submitted,
        ),
    )
    monkeypatch.setattr(worker, "_validate_replay_evidence", lambda *_: None)
    monkeypatch.setattr(worker, "_parse_requests", lambda *_: ({"body": {"max_output_tokens": 1}},))
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda *_: pytest.fail("secret"))
    monkeypatch.setattr(worker, "_workflow", lambda *_args, **_kwargs: pytest.fail("provider"))

    class _Replay:
        def submit_first_adjudication(
            self, run: Path, *, verified_run_state: SpeakerReviewRunState
        ):
            return run, verified_run_state

    monkeypatch.setattr(worker, "_replay_workflow", lambda: _Replay())
    result = worker.submit_first_adjudication(
        environment={
            contract.ENV_ARCHIVE_SHA256: "a" * 64,
            contract.ENV_AUTHORIZATION_ID: "123e4567-e89b-42d3-a456-426614174000",
            contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: "5000000",
            contract.ENV_RUN_ID: RUN_ID,
            contract.ENV_EXPECTED_REQUEST_SHA256: sha256(request).hexdigest(),
            contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: sha256(run_state).hexdigest(),
            contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: worker._set_digest({}),
            contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: worker._set_digest({}),
            contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256: worker._set_digest({}),
            contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256: worker._set_digest(
                {"adjudication-part-0001-requests.jsonl": request}
            ),
        },
        review_root=tmp_path,
    )
    assert result["status"] == "already_submitted"
