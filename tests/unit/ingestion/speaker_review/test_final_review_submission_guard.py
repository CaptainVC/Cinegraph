from __future__ import annotations

from pathlib import Path

import pytest

from cinegraph.common.error_messages import SpeakerReviewErrorMessages
from cinegraph.config import DEFAULT_SPEAKER_REVIEW_CONFIGURATION
from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review.workflow import SpeakerReviewRunState, SpeakerReviewWorkflow
from cinegraph.ports.llm.speaker_review_batch_gateway import BatchSubmission


class _Gateway:
    def __init__(self) -> None:
        self.calls = 0

    def submit(self, *args: object, **kwargs: object) -> BatchSubmission:
        self.calls += 1
        return BatchSubmission("batch", "file", "submitted")

    def retrieve(self, *args: object, **kwargs: object) -> object:
        raise AssertionError

    def download_file(self, *args: object, **kwargs: object) -> str:
        raise AssertionError


def _state() -> SpeakerReviewRunState:
    return SpeakerReviewRunState(
        schema_version=5,
        run_id="speaker-review-0123456789abcdef",
        status=SpeakerReviewRunStatus.FINAL_REVIEW_PREPARED,
        created_at="2026-09-20T00:00:00+00:00",
        updated_at="2026-09-20T00:00:00+00:00",
        candidate_count=1,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        final_review_model="gpt-5.6-sol",
        prompt_version="speaker-review-v1",
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=0.1,
        actual_primary_cost_usd=0.1,
        actual_adjudication_cost_usd=0.1,
        final_review_part_count=1,
    )


def test_final_review_digest_mismatch_reconciles_before_provider_call(tmp_path: Path) -> None:
    request = tmp_path / "final-review-part-0001-requests.jsonl"
    request.write_bytes(b'{"body": {"max_output_tokens": 640}}\n')
    gateway = _Gateway()
    workflow = SpeakerReviewWorkflow(
        gateway=gateway,
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        final_review_model="gpt-5.6-sol",
        primary_reasoning_effort="low",
        adjudication_reasoning_effort="high",
        final_review_reasoning_effort="high",
        expected_final_review_request_sha256="0" * 64,
    )

    with pytest.raises(RuntimeError, match=SpeakerReviewErrorMessages.BATCH_SUBMISSION_RECONCILIATION_REQUIRED):
        workflow._submit_part(run_directory=tmp_path, state=_state(), stage="final-review", part_index=0)
    assert gateway.calls == 0
