from uuid import UUID

import pytest

from cinegraph.adapters.workflow.langgraph.grounded_answer_graph import (
    GroundedAnswerGraphWorkflow,
)
from cinegraph.application.models.grounded_answer import (
    GroundedAnswerQuery,
    GroundedAnswerResult,
    ModelDraft,
)
from cinegraph.common.error_messages import WorkflowErrorMessages
from cinegraph.domain.enums.enum import Language, RightsStatus
from cinegraph.domain.models.transcript.transcript_segment import TranscriptSegment
from tests.factories import make_authenticated_corpus_access_scope, make_episode_ref

SOURCE_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000401")
SOURCE_VERSION_ID = UUID("00000000-0000-0000-0000-000000000501")


class RecordingGroundedAnswerService:
    def __init__(
        self,
        *,
        visible_segments: tuple[TranscriptSegment, ...],
        drafts: list[ModelDraft] | None = None,
        validation_outcomes: list[GroundedAnswerResult | ValueError] | None = None,
    ) -> None:
        self._visible_segments = visible_segments
        self._drafts = list(drafts or [])
        self._validation_outcomes = list(validation_outcomes or [])
        self.calls: list[str] = []

    def retrieve_visible_segments(self, query: GroundedAnswerQuery) -> tuple:
        self.calls.append("retrieve_visible_segments")
        return self._visible_segments

    def draft_answer(self, question: str, visible_segments: tuple) -> ModelDraft:
        self.calls.append("draft_answer")
        return self._drafts.pop(0)

    def validate_draft(
        self, visible_segments: tuple, draft: ModelDraft
    ) -> GroundedAnswerResult:
        self.calls.append("validate_draft")
        outcome = self._validation_outcomes.pop(0)
        if isinstance(outcome, ValueError):
            raise outcome
        return outcome


def segment(*, segment_id: int, start_ms: int, text: str) -> TranscriptSegment:
    return TranscriptSegment(
        segment_id=UUID(int=segment_id),
        source_version_id=SOURCE_VERSION_ID,
        episode=make_episode_ref(),
        start_ms=start_ms,
        end_ms=start_ms + 1_000,
        text=text,
        language=Language.ENGLISH,
        rights_status=RightsStatus.RESTRICTED,
    )


def query(*, question: str = "Why did Luke get stuck?") -> GroundedAnswerQuery:
    return GroundedAnswerQuery(
        question=question,
        episode=make_episode_ref(),
        summary_source_document_id=SOURCE_DOCUMENT_ID,
        profile_watch_state=None,
        corpus_access_scope=make_authenticated_corpus_access_scope(),
        limit=5,
    )


def test_negative_max_regeneration_attempts_are_rejected() -> None:
    service = RecordingGroundedAnswerService(visible_segments=())

    with pytest.raises(ValueError) as exc_info:
        GroundedAnswerGraphWorkflow(service, max_regeneration_attempts=-1)

    assert (
        str(exc_info.value)
        == WorkflowErrorMessages.MAX_REGENERATION_ATTEMPTS_MUST_BE_NON_NEGATIVE
    )


def test_successful_evidence_backed_result_and_stage_call_ordering() -> None:
    match = segment(
        segment_id=1,
        start_ms=10_000,
        text="Luke got his head stuck in the banister.",
    )
    expected_result = GroundedAnswerResult(
        answer="Luke got his head stuck in the banister.",
        citations=(match,),
        is_safe_refusal=False,
    )
    service = RecordingGroundedAnswerService(
        visible_segments=(match,),
        drafts=[ModelDraft(answer="Luke got stuck.", cited_segment_ids=(match.segment_id,))],
        validation_outcomes=[expected_result],
    )
    workflow = GroundedAnswerGraphWorkflow(service)

    result = workflow.execute(query())

    assert result == expected_result
    assert service.calls == [
        "retrieve_visible_segments",
        "draft_answer",
        "validate_draft",
    ]


def test_zero_evidence_refuses_without_draft_or_validation_calls() -> None:
    service = RecordingGroundedAnswerService(visible_segments=())
    workflow = GroundedAnswerGraphWorkflow(service)

    result = workflow.execute(query())

    assert result == GroundedAnswerResult(answer=None, citations=(), is_safe_refusal=True)
    assert service.calls == ["retrieve_visible_segments"]


def test_one_invalid_draft_regenerates_once_then_accepts_valid_draft() -> None:
    match = segment(
        segment_id=1,
        start_ms=10_000,
        text="Luke got his head stuck in the banister.",
    )
    invalid_draft = ModelDraft(answer="Invalid.", cited_segment_ids=(UUID(int=999),))
    valid_draft = ModelDraft(answer="Valid.", cited_segment_ids=(match.segment_id,))
    valid_result = GroundedAnswerResult(
        answer="Valid.", citations=(match,), is_safe_refusal=False
    )
    service = RecordingGroundedAnswerService(
        visible_segments=(match,),
        drafts=[invalid_draft, valid_draft],
        validation_outcomes=[
            ValueError("unknown segment cited"),
            valid_result,
        ],
    )
    workflow = GroundedAnswerGraphWorkflow(service)

    result = workflow.execute(query())

    assert result == valid_result
    assert service.calls == [
        "retrieve_visible_segments",
        "draft_answer",
        "validate_draft",
        "draft_answer",
        "validate_draft",
    ]


def test_repeated_invalid_drafts_exhaust_retry_and_refuse_without_exposing_error() -> None:
    match = segment(
        segment_id=1,
        start_ms=10_000,
        text="Luke got his head stuck in the banister.",
    )
    invalid_draft_one = ModelDraft(answer="Invalid one.", cited_segment_ids=(UUID(int=999),))
    invalid_draft_two = ModelDraft(answer="Invalid two.", cited_segment_ids=(UUID(int=998),))
    service = RecordingGroundedAnswerService(
        visible_segments=(match,),
        drafts=[invalid_draft_one, invalid_draft_two],
        validation_outcomes=[
            ValueError("unknown segment cited"),
            ValueError("unknown segment cited"),
        ],
    )
    workflow = GroundedAnswerGraphWorkflow(service)

    result = workflow.execute(query())

    assert result == GroundedAnswerResult(answer=None, citations=(), is_safe_refusal=True)
    assert service.calls == [
        "retrieve_visible_segments",
        "draft_answer",
        "validate_draft",
        "draft_answer",
        "validate_draft",
    ]
