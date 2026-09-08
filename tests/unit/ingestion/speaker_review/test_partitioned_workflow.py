import json
import threading
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from cinegraph.config import DEFAULT_SPEAKER_REVIEW_CONFIGURATION
from cinegraph.domain.enums.enum import (
    SpeakerReviewDisposition,
    SpeakerReviewRunStatus,
)
from cinegraph.domain.models.transcript import (
    SpeakerReviewCandidate,
    SpeakerReviewDecision,
    SpeakerReviewEvidence,
)
from cinegraph.ingestion.speaker_review.workflow import (
    SpeakerReviewRunState,
    SpeakerReviewWorkflow,
    _submission_path,
    load_run_state,
)
from cinegraph.ports.llm.speaker_review_batch_gateway import (
    BatchSnapshot,
    BatchSubmission,
)


class CompletingPartGateway:
    def __init__(self) -> None:
        self.submitted_paths: list[Path] = []

    def submit(
        self,
        request_filename: str,
        request_bytes: bytes,
        completion_window: str,
        metadata: dict[str, str],
    ) -> BatchSubmission:
        self.submitted_paths.append(Path(request_filename))
        return BatchSubmission("batch-2", "input-2", "validating")

    def retrieve(self, batch_id: str) -> BatchSnapshot:
        assert batch_id == "batch-1"
        return BatchSnapshot(
            batch_id=batch_id,
            status="completed",
            output_file_id="output-1",
            error_file_id=None,
            total_requests=1,
            completed_requests=1,
            failed_requests=0,
        )

    def download_file(self, file_id: str) -> str:
        assert file_id == "output-1"
        return "{}\n"


class CompletedRetryGateway:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text

    def submit(
        self,
        request_filename: str,
        request_bytes: bytes,
        completion_window: str,
        metadata: dict[str, str],
    ) -> BatchSubmission:
        raise AssertionError("No additional retry may be submitted.")

    def retrieve(self, batch_id: str) -> BatchSnapshot:
        assert batch_id == "batch-2"
        return BatchSnapshot(
            batch_id=batch_id,
            status="completed",
            output_file_id="output-2",
            error_file_id=None,
            total_requests=1,
            completed_requests=1,
            failed_requests=0,
        )

    def download_file(self, file_id: str) -> str:
        assert file_id == "output-2"
        return self.output_text


class CountingSubmissionGateway:
    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail
        self.submissions: list[tuple[str, bytes]] = []

    def submit(
        self,
        request_filename: str,
        request_bytes: bytes,
        completion_window: str,
        metadata: dict[str, str],
    ) -> BatchSubmission:
        self.calls += 1
        self.submissions.append((request_filename, request_bytes))
        if self.fail:
            raise RuntimeError("ambiguous provider failure")
        return BatchSubmission("batch-safe", "input-safe", "validating")

    def retrieve(self, batch_id: str) -> BatchSnapshot:
        raise AssertionError("not used")

    def download_file(self, file_id: str) -> str:
        raise AssertionError("not used")


class PrimaryObservationGateway:
    def __init__(self, status: str, snapshot_batch_id: str | None = None) -> None:
        self.status = status
        self.snapshot_batch_id = snapshot_batch_id
        self.retrieve_calls: list[str] = []
        self.download_calls: list[str] = []
        self.submit_calls = 0

    def submit(
        self,
        request_filename: str,
        request_bytes: bytes,
        completion_window: str,
        metadata: dict[str, str],
    ) -> BatchSubmission:
        self.submit_calls += 1
        raise AssertionError("observe_primary_part must never submit")

    def retrieve(self, batch_id: str) -> BatchSnapshot:
        self.retrieve_calls.append(batch_id)
        return BatchSnapshot(
            batch_id=self.snapshot_batch_id or batch_id,
            status=self.status,
            output_file_id="output-1" if self.status == "completed" else None,
            error_file_id="error-1" if self.status == "failed" else None,
            total_requests=2,
            completed_requests=2 if self.status == "completed" else 0,
            failed_requests=2 if self.status == "failed" else 0,
        )

    def download_file(self, file_id: str) -> str:
        self.download_calls.append(file_id)
        return "provider error\n" if file_id == "error-1" else "{}\n"


def _prepared_state(run_id: str = "run-safe") -> SpeakerReviewRunState:
    return SpeakerReviewRunState(
        schema_version=2,
        run_id=run_id,
        status=SpeakerReviewRunStatus.PREPARED,
        created_at="2026-08-15T00:00:00+00:00",
        updated_at="2026-08-15T00:00:00+00:00",
        candidate_count=1,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        prompt_version="speaker-review-v1",
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=1.0,
        actual_primary_cost_usd=0.0,
        actual_adjudication_cost_usd=0.0,
        primary_part_count=1,
    )


def _workflow(
    gateway: CountingSubmissionGateway,
    configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
) -> SpeakerReviewWorkflow:
    return SpeakerReviewWorkflow(
        gateway=gateway,
        configuration=configuration,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        final_review_model="gpt-5.6-sol",
        primary_reasoning_effort="low",
        adjudication_reasoning_effort="medium",
        final_review_reasoning_effort="high",
    )


def test_completed_part_submits_only_the_next_part(tmp_path: Path) -> None:
    gateway = CompletingPartGateway()
    workflow = SpeakerReviewWorkflow(
        gateway=gateway,
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        final_review_model="gpt-5.6-sol",
        primary_reasoning_effort="low",
        adjudication_reasoning_effort="medium",
        final_review_reasoning_effort="high",
    )
    next_path = tmp_path / "primary-part-0002-requests.jsonl"
    next_path.write_text("{}\n", encoding="utf-8")
    state = SpeakerReviewRunState(
        schema_version=2,
        run_id="run-1",
        status=SpeakerReviewRunStatus.PRIMARY_SUBMITTED,
        created_at="2026-08-15T00:00:00+00:00",
        updated_at="2026-08-15T00:00:00+00:00",
        candidate_count=1,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        prompt_version="speaker-review-v1",
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=1.0,
        actual_primary_cost_usd=0.0,
        actual_adjudication_cost_usd=0.0,
        primary_batch_id="batch-1",
        primary_input_file_id="input-1",
        primary_part_count=2,
        primary_batch_ids=("batch-1",),
        primary_input_file_ids=("input-1",),
    )

    updated = workflow.advance(tmp_path, state)

    assert updated.status is SpeakerReviewRunStatus.PRIMARY_SUBMITTED
    assert updated.primary_completed_part_count == 1
    assert updated.primary_batch_ids == ("batch-1", "batch-2")
    assert updated.primary_input_file_ids == ("input-1", "input-2")
    assert gateway.submitted_paths == [Path(next_path.name)]
    assert (tmp_path / "primary-part-0001-output.jsonl").read_text() == "{}\n"


def _submitted_state(
    *,
    status: SpeakerReviewRunStatus = SpeakerReviewRunStatus.PRIMARY_SUBMITTED,
    primary_completed_part_count: int = 0,
) -> SpeakerReviewRunState:
    return SpeakerReviewRunState(
        schema_version=2,
        run_id="run-observe",
        status=status,
        created_at="2026-08-15T00:00:00+00:00",
        updated_at="2026-08-15T00:00:00+00:00",
        candidate_count=1,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        prompt_version="speaker-review-v1",
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=1.0,
        actual_primary_cost_usd=0.0,
        actual_adjudication_cost_usd=0.0,
        primary_batch_id="batch-1",
        primary_input_file_id="input-1",
        primary_part_count=2,
        primary_completed_part_count=primary_completed_part_count,
        primary_batch_ids=("batch-1",),
        primary_input_file_ids=("input-1",),
    )


def test_observe_primary_returns_pending_without_download_or_submit(
    tmp_path: Path,
) -> None:
    gateway = PrimaryObservationGateway("validating")
    workflow = _workflow(gateway)  # type: ignore[arg-type]
    state = _submitted_state()

    updated = workflow.observe_primary_part(tmp_path, state)

    assert updated is state
    assert gateway.retrieve_calls == ["batch-1"]
    assert gateway.download_calls == []
    assert gateway.submit_calls == 0
    assert not (tmp_path / "primary-part-0001-output.jsonl").exists()


def test_observe_primary_completes_only_active_part_of_multi_part_run(
    tmp_path: Path,
) -> None:
    gateway = PrimaryObservationGateway("completed")
    workflow = _workflow(gateway)  # type: ignore[arg-type]
    state = _submitted_state()

    updated = workflow.observe_primary_part(tmp_path, state)

    assert updated.status is SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED
    assert updated.primary_completed_part_count == 1
    assert gateway.retrieve_calls == ["batch-1"]
    assert gateway.download_calls == ["output-1"]
    assert gateway.submit_calls == 0
    assert (tmp_path / "primary-part-0001-output.jsonl").read_text() == "{}\n"
    assert (load_run_state(tmp_path / "run-state.json")).status is (
        SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED
    )
    assert not (tmp_path / "primary-part-0002-output.jsonl").exists()
    assert not (tmp_path / "primary-verdicts.jsonl").exists()


def test_observe_primary_persists_failure_and_downloads_only_error(
    tmp_path: Path,
) -> None:
    gateway = PrimaryObservationGateway("failed")
    workflow = _workflow(gateway)  # type: ignore[arg-type]

    updated = workflow.observe_primary_part(tmp_path, _submitted_state())

    assert updated.status is SpeakerReviewRunStatus.FAILED
    assert gateway.retrieve_calls == ["batch-1"]
    assert gateway.download_calls == ["error-1"]
    assert gateway.submit_calls == 0
    assert (load_run_state(tmp_path / "run-state.json")).status is (
        SpeakerReviewRunStatus.FAILED
    )


def test_observe_primary_rejects_corrupt_active_batch_shape_before_provider_call(
    tmp_path: Path,
) -> None:
    gateway = PrimaryObservationGateway("completed")
    workflow = _workflow(gateway)  # type: ignore[arg-type]
    corrupt_state = replace(
        _submitted_state(),
        primary_batch_ids=("batch-1", "unexpected-batch"),
    )

    with pytest.raises(RuntimeError, match="reconciliation"):
        workflow.observe_primary_part(tmp_path, corrupt_state)

    assert gateway.retrieve_calls == []
    assert gateway.download_calls == []
    assert gateway.submit_calls == 0


def test_observe_primary_rejects_mismatched_provider_snapshot_before_download(
    tmp_path: Path,
) -> None:
    gateway = PrimaryObservationGateway("completed", snapshot_batch_id="batch-other")
    workflow = _workflow(gateway)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="reconciliation"):
        workflow.observe_primary_part(tmp_path, _submitted_state())

    assert gateway.retrieve_calls == ["batch-1"]
    assert gateway.download_calls == []
    assert gateway.submit_calls == 0
    assert not (tmp_path / "run-state.json").exists()


def test_observe_primary_maps_conflicting_output_to_reconciliation(
    tmp_path: Path,
) -> None:
    gateway = PrimaryObservationGateway("completed")
    workflow = _workflow(gateway)  # type: ignore[arg-type]
    (tmp_path / "primary-part-0001-output.jsonl").write_text(
        '{"different":true}\n',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="reconciliation"):
        workflow.observe_primary_part(tmp_path, _submitted_state())

    assert gateway.retrieve_calls == ["batch-1"]
    assert gateway.download_calls == ["output-1"]
    assert gateway.submit_calls == 0
    assert not (tmp_path / "run-state.json").exists()


@pytest.mark.parametrize(
    "status",
    [SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED, SpeakerReviewRunStatus.FAILED],
)
def test_observe_primary_terminal_states_are_idempotent_without_provider_calls(
    tmp_path: Path,
    status: SpeakerReviewRunStatus,
) -> None:
    gateway = PrimaryObservationGateway("completed")
    workflow = _workflow(gateway)  # type: ignore[arg-type]
    state = _submitted_state(status=status, primary_completed_part_count=1)

    assert workflow.observe_primary_part(tmp_path, state) is state
    assert gateway.retrieve_calls == []
    assert gateway.download_calls == []
    assert gateway.submit_calls == 0


def test_submission_snapshot_is_reused_without_a_second_paid_submit(
    tmp_path: Path,
) -> None:
    gateway = CountingSubmissionGateway()
    workflow = SpeakerReviewWorkflow(
        gateway=gateway,
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        final_review_model="gpt-5.6-sol",
        primary_reasoning_effort="low",
        adjudication_reasoning_effort="medium",
        final_review_reasoning_effort="high",
    )
    request = tmp_path / "primary-part-0001-requests.jsonl"
    request.write_text("{}\n", encoding="utf-8")
    state = SpeakerReviewRunState(
        schema_version=2,
        run_id="run-safe",
        status=SpeakerReviewRunStatus.PREPARED,
        created_at="2026-08-15T00:00:00+00:00",
        updated_at="2026-08-15T00:00:00+00:00",
        candidate_count=1,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        prompt_version="speaker-review-v1",
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=1.0,
        actual_primary_cost_usd=0.0,
        actual_adjudication_cost_usd=0.0,
        primary_part_count=1,
    )
    first = workflow.submit_primary(tmp_path, state)
    second = workflow.submit_primary(tmp_path, state)
    assert first.primary_batch_id == second.primary_batch_id == "batch-safe"
    assert gateway.calls == 1


def test_submission_journal_and_gateway_share_one_immutable_request_snapshot(
    tmp_path: Path,
) -> None:
    request = tmp_path / "primary-part-0001-requests.jsonl"
    original = b'{"custom_id":"original"}\n'
    request.write_bytes(original)

    class MutatingGateway(CountingSubmissionGateway):
        def submit(
            self,
            request_filename: str,
            request_bytes: bytes,
            completion_window: str,
            metadata: dict[str, str],
        ) -> BatchSubmission:
            request.write_bytes(b'{"custom_id":"replacement"}\n')
            return super().submit(
                request_filename,
                request_bytes,
                completion_window,
                metadata,
            )

    gateway = MutatingGateway()
    _workflow(gateway).submit_primary(tmp_path, _prepared_state())

    intent = json.loads(
        _submission_path(tmp_path, "primary", 0, "intent").read_text(
            encoding="utf-8"
        )
    )
    assert gateway.submissions == [(request.name, original)]
    assert intent["binding"]["request_sha256"] == sha256(original).hexdigest()


def test_ambiguous_submission_intent_blocks_automatic_resubmit(tmp_path: Path) -> None:
    gateway = CountingSubmissionGateway(fail=True)
    workflow = SpeakerReviewWorkflow(
        gateway=gateway,
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        final_review_model="gpt-5.6-sol",
        primary_reasoning_effort="low",
        adjudication_reasoning_effort="medium",
        final_review_reasoning_effort="high",
    )
    (tmp_path / "primary-part-0001-requests.jsonl").write_text("{}\n", encoding="utf-8")
    state = SpeakerReviewRunState(
        schema_version=2,
        run_id="run-ambiguous",
        status=SpeakerReviewRunStatus.PREPARED,
        created_at="2026-08-15T00:00:00+00:00",
        updated_at="2026-08-15T00:00:00+00:00",
        candidate_count=1,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        prompt_version="speaker-review-v1",
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=1.0,
        actual_primary_cost_usd=0.0,
        actual_adjudication_cost_usd=0.0,
        primary_part_count=1,
    )
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        workflow.submit_primary(tmp_path, state)
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        workflow.submit_primary(tmp_path, state)
    assert gateway.calls == 1


def test_changed_request_and_transport_binding_cannot_reuse_snapshot(
    tmp_path: Path,
) -> None:
    request = tmp_path / "primary-part-0001-requests.jsonl"
    request.write_text("{}\n", encoding="utf-8")
    gateway = CountingSubmissionGateway()
    state = _prepared_state()
    _workflow(gateway).submit_primary(tmp_path, state)
    request.write_text('{"changed":true}\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        _workflow(gateway).submit_primary(tmp_path, state)
    request.write_text("{}\n", encoding="utf-8")
    changed = replace(
        DEFAULT_SPEAKER_REVIEW_CONFIGURATION, batch_completion_window="1h"
    )
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        _workflow(gateway, changed).submit_primary(tmp_path, state)
    assert gateway.calls == 1


@pytest.mark.parametrize(
    "raw",
    [
        b'{"binding":{},"binding":{},"status":"intent"}\n',
        b"x" * 4097,
    ],
)
def test_malformed_submission_intent_is_fail_closed(tmp_path: Path, raw: bytes) -> None:
    (tmp_path / "primary-part-0001-requests.jsonl").write_text("{}\n", encoding="utf-8")
    _submission_path(tmp_path, "primary", 0, "intent").write_bytes(raw)
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        _workflow(CountingSubmissionGateway()).submit_primary(
            tmp_path, _prepared_state()
        )


def test_orphan_completed_submission_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "primary-part-0001-requests.jsonl").write_text("{}\n", encoding="utf-8")
    gateway = CountingSubmissionGateway()
    _workflow(gateway).submit_primary(tmp_path, _prepared_state())
    _submission_path(tmp_path, "primary", 0, "intent").unlink()
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        _workflow(gateway).submit_primary(tmp_path, _prepared_state())
    assert gateway.calls == 1


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_submission_journal_rejects_links(tmp_path: Path, link_kind: str) -> None:
    (tmp_path / "primary-part-0001-requests.jsonl").write_text("{}\n", encoding="utf-8")
    target = tmp_path / "outside"
    target.write_text("x", encoding="utf-8")
    journal = _submission_path(tmp_path, "primary", 0, "intent")
    try:
        if link_kind == "symlink":
            journal.symlink_to(target)
        else:
            journal.hardlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip(f"{link_kind} unavailable")
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        _workflow(CountingSubmissionGateway()).submit_primary(
            tmp_path, _prepared_state()
        )


def test_intent_must_match_completed_submission(tmp_path: Path) -> None:
    (tmp_path / "primary-part-0001-requests.jsonl").write_text("{}\n", encoding="utf-8")
    gateway = CountingSubmissionGateway()
    state = _prepared_state()
    _workflow(gateway).submit_primary(tmp_path, state)
    intent_path = _submission_path(tmp_path, "primary", 0, "intent")
    payload = json.loads(intent_path.read_text())
    payload["binding"]["run_id"] = "other-run"
    intent_path.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        _workflow(gateway).submit_primary(tmp_path, state)
    assert gateway.calls == 1


def test_resume_reuses_submission_after_run_state_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cinegraph.ingestion.speaker_review import workflow as workflow_module

    (tmp_path / "primary-part-0001-requests.jsonl").write_text("{}\n", encoding="utf-8")
    gateway = CountingSubmissionGateway()
    state = _prepared_state()

    def fail_save(*args: object) -> None:
        raise OSError("synthetic state-write interruption")

    with monkeypatch.context() as scoped:
        scoped.setattr(workflow_module, "save_run_state", fail_save)
        with pytest.raises(OSError, match="synthetic"):
            _workflow(gateway).submit_primary(tmp_path, state)

    resumed = _workflow(gateway).submit_primary(tmp_path, state)
    assert resumed.primary_batch_id == "batch-safe"
    assert gateway.calls == 1


def test_concurrent_exact_attempt_has_one_gateway_call(tmp_path: Path) -> None:
    (tmp_path / "primary-part-0001-requests.jsonl").write_text("{}\n", encoding="utf-8")
    gateway = CountingSubmissionGateway()
    state = _prepared_state()
    results: list[object] = []

    def attempt() -> None:
        try:
            results.append(
                _workflow(gateway)._submit_part(
                    run_directory=tmp_path, state=state, stage="primary", part_index=0
                )
            )
        except RuntimeError as error:
            results.append(error)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert gateway.calls == 1
    assert len(results) == 2


def test_terminal_run_retries_only_missing_final_verdict_once(tmp_path: Path) -> None:
    gateway = CompletingPartGateway()
    workflow = SpeakerReviewWorkflow(
        gateway=gateway,
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        final_review_model="gpt-5.6-sol",
        primary_reasoning_effort="low",
        adjudication_reasoning_effort="medium",
        final_review_reasoning_effort="high",
    )
    candidate = SpeakerReviewCandidate(
        candidate_id="S01E01-C0001-L00003-abcdef1234",
        source_filename="episode.script-aligned.srt",
        source_sha256="a" * 64,
        season_number=1,
        episode_number=1,
        cue_number=1,
        line_number=3,
        proposed_speaker="CLAIRE",
        dialogue_text="Kids, breakfast!",
        allowed_speakers=("CLAIRE", "PHIL"),
        evidence=(
            SpeakerReviewEvidence(
                evidence_id="script-order-1",
                source="screenplay",
                speaker="CLAIRE",
                text="Kids, breakfast!",
                similarity_score=100.0,
            ),
        ),
    )
    decision = SpeakerReviewDecision(
        candidate_id=candidate.candidate_id,
        disposition=SpeakerReviewDisposition.NEEDS_HUMAN,
        speaker=None,
        reason="Final reviewer did not return structured output.",
        primary_verdicts=(),
    )
    (tmp_path / "candidates.jsonl").write_text(
        json.dumps(candidate.to_dict()) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "final-decisions.jsonl").write_text(
        json.dumps(decision.to_dict()) + "\n",
        encoding="utf-8",
    )
    incomplete = {
        "custom_id": f"{candidate.candidate_id}::final-review",
        "response": {
            "status_code": 200,
            "body": {
                "model": "gpt-5.6-sol",
                "output": [],
                "usage": {"input_tokens": 1_000, "output_tokens": 1_200},
            },
        },
    }
    (tmp_path / "final-review-part-0001-output.jsonl").write_text(
        json.dumps(incomplete) + "\n",
        encoding="utf-8",
    )
    state = SpeakerReviewRunState(
        schema_version=2,
        run_id="run-1",
        status=SpeakerReviewRunStatus.NEEDS_HUMAN,
        created_at="2026-08-15T00:00:00+00:00",
        updated_at="2026-08-15T00:00:00+00:00",
        candidate_count=1,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        final_review_model="gpt-5.6-sol",
        prompt_version="speaker-review-v1",
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=1.0,
        actual_primary_cost_usd=0.10,
        actual_adjudication_cost_usd=0.10,
        actual_final_review_cost_usd=0.0,
        final_review_batch_id="batch-1",
        final_review_input_file_id="input-1",
        final_review_part_count=1,
        final_review_completed_part_count=1,
        final_review_batch_ids=("batch-1",),
        final_review_input_file_ids=("input-1",),
        needs_human=1,
    )

    updated = workflow.retry_incomplete_final_review(tmp_path, state)

    request = json.loads(
        (tmp_path / "final-review-part-0002-requests.jsonl").read_text(
            encoding="utf-8"
        )
    )
    assert updated.status is SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED
    assert updated.final_review_retry_count == 1
    assert updated.final_review_part_count == 2
    assert updated.final_review_completed_part_count == 1
    assert updated.actual_final_review_cost_usd == 0.0205
    assert request["custom_id"].endswith("::final-review-retry-1")
    assert request["body"]["max_output_tokens"] == 2_400
    assert gateway.submitted_paths == [
        Path("final-review-part-0002-requests.jsonl")
    ]


def test_completed_retry_versions_immutable_audit_artifacts(tmp_path: Path) -> None:
    run_id = "speaker-review-0123456789abcdef"
    run_directory = tmp_path / "corpus" / "review-runs" / run_id
    run_directory.mkdir(parents=True)
    candidate = SpeakerReviewCandidate(
        candidate_id="S01E01-C0001-L00003-abcdef1234",
        source_filename="episode.script-aligned.srt",
        source_sha256="a" * 64,
        season_number=1,
        episode_number=1,
        cue_number=1,
        line_number=3,
        proposed_speaker="CLAIRE",
        dialogue_text="Kids, breakfast!",
        allowed_speakers=("CLAIRE", "PHIL"),
        evidence=(
            SpeakerReviewEvidence(
                evidence_id="script-order-1",
                source="screenplay",
                speaker="CLAIRE",
                text="Kids, breakfast!",
                similarity_score=100.0,
            ),
        ),
    )
    decision = SpeakerReviewDecision(
        candidate_id=candidate.candidate_id,
        disposition=SpeakerReviewDisposition.NEEDS_HUMAN,
        speaker=None,
        reason="Final reviewer did not return structured output.",
        primary_verdicts=(),
    )
    incomplete = {
        "custom_id": f"{candidate.candidate_id}::final-review",
        "response": {
            "status_code": 200,
            "body": {
                "model": "gpt-5.6-sol",
                "output": [],
                "usage": {"input_tokens": 1_000, "output_tokens": 1_200},
            },
        },
    }
    retry_payload = {
        "candidate_id": candidate.candidate_id,
        "action": "needs_review",
        "speaker": "CLAIRE",
        "confidence": 0.80,
        "evidence_ids": ["script-order-1"],
        "rationale": "The evidence remains ambiguous.",
    }
    completed = {
        "custom_id": f"{candidate.candidate_id}::final-review-retry-1",
        "response": {
            "status_code": 200,
            "body": {
                "id": "response-2",
                "model": "gpt-5.6-sol",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(retry_payload),
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 1_000, "output_tokens": 100},
            },
        },
    }
    gateway = CompletedRetryGateway(json.dumps(completed) + "\n")
    workflow = SpeakerReviewWorkflow(
        gateway=gateway,
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        final_review_model="gpt-5.6-sol",
        primary_reasoning_effort="low",
        adjudication_reasoning_effort="medium",
        final_review_reasoning_effort="high",
    )
    (run_directory / "candidates.jsonl").write_text(
        json.dumps(candidate.to_dict()) + "\n",
        encoding="utf-8",
    )
    (run_directory / "final-decisions.jsonl").write_text(
        json.dumps(decision.to_dict()) + "\n",
        encoding="utf-8",
    )
    (run_directory / "source-manifest.json").write_text(
        json.dumps({"sources": {}}) + "\n",
        encoding="utf-8",
    )
    (run_directory / "final-review-part-0001-output.jsonl").write_text(
        json.dumps(incomplete) + "\n",
        encoding="utf-8",
    )
    (run_directory / "final-review-verdicts.jsonl").write_text(
        "original-verdict-artifact\n",
        encoding="utf-8",
    )
    (run_directory / "post-final-decisions.jsonl").write_text(
        "original-decision-artifact\n",
        encoding="utf-8",
    )
    (run_directory / "human-review-queue.json").write_text("[]\n", encoding="utf-8")
    (run_directory / "remaining-human-review-queue.json").write_text(
        "original-queue-artifact\n",
        encoding="utf-8",
    )
    state = SpeakerReviewRunState(
        schema_version=2,
        run_id=run_id,
        status=SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED,
        created_at="2026-08-15T00:00:00+00:00",
        updated_at="2026-08-15T00:00:00+00:00",
        candidate_count=1,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        final_review_model="gpt-5.6-sol",
        prompt_version="speaker-review-v1",
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=1.0,
        actual_primary_cost_usd=0.10,
        actual_adjudication_cost_usd=0.10,
        actual_final_review_cost_usd=0.0205,
        final_review_batch_id="batch-2",
        final_review_input_file_id="input-2",
        final_review_part_count=2,
        final_review_completed_part_count=1,
        final_review_batch_ids=("batch-1", "batch-2"),
        final_review_input_file_ids=("input-1", "input-2"),
        final_review_retry_count=1,
        needs_human=1,
    )

    updated = workflow.advance(run_directory, state)

    assert updated.status is SpeakerReviewRunStatus.NEEDS_HUMAN
    assert updated.final_review_completed_part_count == 2
    assert (run_directory / "final-review-verdicts.jsonl").read_text() == (
        "original-verdict-artifact\n"
    )
    assert (run_directory / "post-final-decisions.jsonl").read_text() == (
        "original-decision-artifact\n"
    )
    assert (run_directory / "remaining-human-review-queue.json").read_text() == (
        "original-queue-artifact\n"
    )
    assert (run_directory / "final-review-verdicts-retry-1.jsonl").exists()
    assert (run_directory / "post-final-decisions-retry-1.jsonl").exists()
    assert (run_directory / "remaining-human-review-queue-retry-1.json").exists()


def test_reconcile_costs_prices_usage_from_completed_raw_outputs(
    tmp_path: Path,
) -> None:
    gateway = CompletingPartGateway()
    workflow = SpeakerReviewWorkflow(
        gateway=gateway,
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        final_review_model="gpt-5.6-sol",
        primary_reasoning_effort="low",
        adjudication_reasoning_effort="medium",
        final_review_reasoning_effort="high",
    )
    raw_output = {
        "custom_id": "candidate::primary-a",
        "response": {
            "status_code": 200,
            "body": {
                "model": "gpt-5.6-luna",
                "output": [],
                "usage": {"input_tokens": 1_000, "output_tokens": 1_000},
            },
        },
    }
    (tmp_path / "primary-part-0001-output.jsonl").write_text(
        json.dumps(raw_output) + "\n",
        encoding="utf-8",
    )
    state = SpeakerReviewRunState(
        schema_version=2,
        run_id="run-1",
        status=SpeakerReviewRunStatus.NEEDS_HUMAN,
        created_at="2026-08-15T00:00:00+00:00",
        updated_at="2026-08-15T00:00:00+00:00",
        candidate_count=1,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        prompt_version="speaker-review-v1",
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=1.0,
        actual_primary_cost_usd=0.0,
        actual_adjudication_cost_usd=0.0,
        primary_part_count=1,
        primary_completed_part_count=1,
    )

    updated = workflow.reconcile_completed_costs(tmp_path, state)

    assert updated.actual_primary_cost_usd == 0.0007
    assert updated.actual_adjudication_cost_usd == 0.0
    assert updated.actual_final_review_cost_usd == 0.0
