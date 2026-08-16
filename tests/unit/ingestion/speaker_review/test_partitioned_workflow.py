import json
from pathlib import Path

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
        request_path: Path,
        completion_window: str,
        metadata: dict[str, str],
    ) -> BatchSubmission:
        self.submitted_paths.append(request_path)
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
        request_path: Path,
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
    assert gateway.submitted_paths == [next_path]
    assert (tmp_path / "primary-part-0001-output.jsonl").read_text() == "{}\n"


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
        tmp_path / "final-review-part-0002-requests.jsonl"
    ]


def test_completed_retry_versions_immutable_audit_artifacts(tmp_path: Path) -> None:
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
    (tmp_path / "candidates.jsonl").write_text(
        json.dumps(candidate.to_dict()) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "final-decisions.jsonl").write_text(
        json.dumps(decision.to_dict()) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "source-manifest.json").write_text(
        json.dumps({"sources": {}}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "final-review-part-0001-output.jsonl").write_text(
        json.dumps(incomplete) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "final-review-verdicts.jsonl").write_text(
        "original-verdict-artifact\n",
        encoding="utf-8",
    )
    (tmp_path / "post-final-decisions.jsonl").write_text(
        "original-decision-artifact\n",
        encoding="utf-8",
    )
    (tmp_path / "human-review-queue.json").write_text("[]\n", encoding="utf-8")
    (tmp_path / "remaining-human-review-queue.json").write_text(
        "original-queue-artifact\n",
        encoding="utf-8",
    )
    state = SpeakerReviewRunState(
        schema_version=2,
        run_id="run-1",
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

    updated = workflow.advance(tmp_path, state)

    assert updated.status is SpeakerReviewRunStatus.NEEDS_HUMAN
    assert updated.final_review_completed_part_count == 2
    assert (tmp_path / "final-review-verdicts.jsonl").read_text() == (
        "original-verdict-artifact\n"
    )
    assert (tmp_path / "post-final-decisions.jsonl").read_text() == (
        "original-decision-artifact\n"
    )
    assert (tmp_path / "remaining-human-review-queue.json").read_text() == (
        "original-queue-artifact\n"
    )
    assert (tmp_path / "final-review-verdicts-retry-1.jsonl").exists()
    assert (tmp_path / "post-final-decisions-retry-1.jsonl").exists()
    assert (tmp_path / "remaining-human-review-queue-retry-1.json").exists()


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
