from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from scripts import private_speaker_review_final_review_submission_contract as contract
from scripts import submit_final_private_speaker_review_workspace as worker

from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review.workflow import SpeakerReviewRunState

RUN_ID = "speaker-review-0123456789abcdef"


def _state(
    status: SpeakerReviewRunStatus = SpeakerReviewRunStatus.FINAL_REVIEW_PREPARED,
) -> SpeakerReviewRunState:
    submitted = status is SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED
    return SpeakerReviewRunState(
        schema_version=5,
        run_id=RUN_ID,
        status=status,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        candidate_count=2,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        final_review_model="gpt-5.6-sol",
        prompt_version="speaker-review-v1",
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=0.25,
        actual_primary_cost_usd=0.10,
        actual_adjudication_cost_usd=0.20,
        primary_part_count=1,
        primary_completed_part_count=1,
        adjudication_part_count=1,
        adjudication_completed_part_count=1,
        final_review_part_count=2,
        final_review_batch_id="private-batch" if submitted else None,
        final_review_input_file_id="private-file" if submitted else None,
        final_review_batch_ids=("private-batch",) if submitted else (),
        final_review_input_file_ids=("private-file",) if submitted else (),
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
            "final-review-part-0001-requests.jsonl": (
                b'{"body":{"max_output_tokens":10}}\n'
            ),
            "final-review-part-0002-requests.jsonl": (
                b'{"body":{"max_output_tokens":10}}\n'
            ),
        }
        if contents is None
        else contents
    )
    monkeypatch.setattr(worker, "_run_directory", lambda *_: tmp_path)
    monkeypatch.setattr(worker, "_inventory", lambda *_: (snapshot, state))
    monkeypatch.setattr(worker, "_digest_bindings", lambda *_: None)
    monkeypatch.setattr(worker, "_expected_request_hash", lambda *_: "b" * 64)
    monkeypatch.setattr(
        worker,
        "_parse_requests",
        lambda *_: (
            {"body": {"max_output_tokens": 10}},
            {"body": {"max_output_tokens": 10}},
        ),
    )
    monkeypatch.setattr(worker, "estimate_batch_cost_usd", lambda **_: 0.001)
    return snapshot


def test_expected_prepared_inventory_allows_whole_journal_names_not_characters() -> None:
    required, optional = worker._expected_names(_state())

    assert "run-state.json" in required
    assert optional >= {
        ".final-review-part-0001-submission-intent.json",
        ".final-review-part-0001-submission-completed.json",
    }
    assert "f" not in optional


def test_prepared_recovery_authenticates_original_journal_set() -> None:
    state = _state()
    contents = {
        "run-state.json": b"state\n",
        "final-review-part-0001-requests.jsonl": b"request\n",
        ".primary-part-0001-submission-intent.json": b"prior\n",
        ".final-review-part-0001-submission-intent.json": b"intent\n",
        ".final-review-part-0001-submission-completed.json": b"completed\n",
    }
    artifacts, journals, outputs, derived = worker._classes(contents)
    prior_journals = {
        name: raw for name, raw in journals.items() if name.startswith(".primary-")
    }
    environment = {
        contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: worker._sha(contents["run-state.json"]),
        contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: worker._set_digest(artifacts),
        contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: worker._set_digest(prior_journals),
        contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256: worker._set_digest(outputs),
        contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256: worker._set_digest(derived),
    }

    worker._digest_bindings(contents, environment, state)

    with pytest.raises(worker.FinalReviewSubmissionWorkerError, match="checkpoint changed"):
        worker._digest_bindings(
            contents,
            environment,
            _state(SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED),
        )


def test_submitted_journals_are_parsed_and_bound_to_state(tmp_path: Path) -> None:
    run = tmp_path / RUN_ID
    run.mkdir()
    request = b'{"body":{"max_output_tokens":10}}\n'
    state = _state(SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED)
    binding = worker._binding(request, state)
    intent = {"binding": binding, "status": "intent"}
    completed = {
        "batch_id": "private-batch",
        "binding": binding,
        "input_file_id": "private-file",
        "status": "submitted",
    }
    intent_path = run / ".final-review-part-0001-submission-intent.json"
    completed_path = run / ".final-review-part-0001-submission-completed.json"
    intent_path.write_bytes(
        (json.dumps(intent, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()
    )
    completed_path.write_bytes(
        (
            json.dumps(completed, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode()
    )

    worker._validate_journals(
        run,
        {"final-review-part-0001-requests.jsonl": request},
        state,
    )

    completed["batch_id"] = "tampered"
    completed_path.write_bytes(
        (
            json.dumps(completed, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode()
    )
    with pytest.raises(worker.FinalReviewSubmissionWorkerError, match="evidence invalid"):
        worker._validate_journals(
            run,
            {"final-review-part-0001-requests.jsonl": request},
            state,
        )


def test_fresh_submission_reads_secret_only_after_preflight_and_uses_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    _patch_preflight(monkeypatch, tmp_path, state)
    events: list[str] = []
    updated = replace(
        state,
        status=SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED,
        final_review_batch_id="private-batch",
        final_review_input_file_id="private-file",
        final_review_batch_ids=("private-batch",),
        final_review_input_file_ids=("private-file",),
    )

    class Graph:
        def final_review(
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

    result = worker.submit_final_review(environment=_environment(), review_root=tmp_path)

    assert events == ["secret", "provider"]
    assert result["status"] == "submitted"
    assert result["run_status"] == "final_review_submitted"
    assert result["submitted_part_count"] == 1


def test_submitted_replay_is_provider_and_secret_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state(SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED)
    _patch_preflight(monkeypatch, tmp_path, state)
    monkeypatch.setattr(worker, "_validate_journals", lambda *_: None)
    monkeypatch.setattr(
        worker,
        "read_stable_openai_secret",
        lambda *_: pytest.fail("secret must remain closed"),
    )
    monkeypatch.setattr(worker, "_workflow", lambda *_args, **_kwargs: pytest.fail("provider"))

    class Replay:
        def final_review(
            self,
            run: Path,
            *,
            verified_run_state: SpeakerReviewRunState,
        ) -> tuple[Path, SpeakerReviewRunState]:
            return run, verified_run_state

    monkeypatch.setattr(worker, "_replay_workflow", lambda **_: Replay())

    result = worker.submit_final_review(environment=_environment(), review_root=tmp_path)

    assert result["status"] == "already_submitted"
    assert result["run_status"] == "final_review_submitted"


@pytest.mark.parametrize(
    "journal_name",
    [
        ".final-review-part-0001-submission-intent.json",
        ".final-review-part-0001-submission-completed.json",
    ],
)
def test_single_journal_reconciles_without_secret_or_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    journal_name: str,
) -> None:
    contents = {
        "run-state.json": b"state\n",
        "final-review-part-0001-requests.jsonl": b'{"body":{"max_output_tokens":10}}\n',
        "final-review-part-0002-requests.jsonl": b'{"body":{"max_output_tokens":10}}\n',
        journal_name: b"journal\n",
    }
    _patch_preflight(monkeypatch, tmp_path, _state(), contents)
    monkeypatch.setattr(
        worker,
        "read_stable_openai_secret",
        lambda *_: pytest.fail("secret must remain closed"),
    )
    monkeypatch.setattr(worker, "_workflow", lambda *_args, **_kwargs: pytest.fail("provider"))

    result = worker.submit_final_review(environment=_environment(), review_root=tmp_path)

    assert result["status"] == "reconciliation_required"
    assert result["run_status"] == "final_review_prepared"
    assert result["submitted_part_count"] == 0


def test_two_journals_recover_through_provider_disabled_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    contents = {
        "run-state.json": b"state\n",
        "final-review-part-0001-requests.jsonl": b'{"body":{"max_output_tokens":10}}\n',
        "final-review-part-0002-requests.jsonl": b'{"body":{"max_output_tokens":10}}\n',
        ".final-review-part-0001-submission-intent.json": b"intent\n",
        ".final-review-part-0001-submission-completed.json": b"completed\n",
    }
    _patch_preflight(monkeypatch, tmp_path, state, contents)
    monkeypatch.setattr(
        worker,
        "read_stable_openai_secret",
        lambda *_: pytest.fail("secret must remain closed"),
    )
    completed_record = {
        "batch_id": "private-batch",
        "binding": worker._binding(contents["final-review-part-0001-requests.jsonl"], state),
        "input_file_id": "private-file",
        "status": "submitted",
    }

    def read_record(_path: Path, *, completed: bool = False) -> dict[str, object]:
        if completed:
            return completed_record
        return {"binding": completed_record["binding"], "status": "intent"}

    monkeypatch.setattr(worker, "_read_submission_record", read_record)
    recovered = replace(
        state,
        status=SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED,
        final_review_batch_id="private-batch",
        final_review_input_file_id="private-file",
        final_review_batch_ids=("private-batch",),
        final_review_input_file_ids=("private-file",),
    )

    class Replay:
        def final_review(
            self,
            run: Path,
            *,
            verified_run_state: SpeakerReviewRunState,
        ) -> tuple[Path, SpeakerReviewRunState]:
            assert verified_run_state is state
            return run, recovered

    monkeypatch.setattr(worker, "_replay_workflow", lambda **_: Replay())

    result = worker.submit_final_review(environment=_environment(), review_root=tmp_path)

    assert result["status"] == "submitted"
    assert result["run_status"] == "final_review_submitted"


def test_budget_rejection_happens_before_secret_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_preflight(monkeypatch, tmp_path, _state())
    monkeypatch.setattr(worker, "estimate_batch_cost_usd", lambda **_: 4.8)
    monkeypatch.setattr(
        worker,
        "read_stable_openai_secret",
        lambda *_: pytest.fail("secret must remain closed"),
    )

    with pytest.raises(worker.FinalReviewSubmissionWorkerError, match="authorization"):
        worker.submit_final_review(environment=_environment(), review_root=tmp_path)
