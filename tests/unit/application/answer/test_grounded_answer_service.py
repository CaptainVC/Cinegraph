from uuid import UUID

import pytest

from cinegraph.application.models.grounded_answer import (
    GroundedAnswerQuery,
    ModelDraft,
    ModelRequest,
)
from cinegraph.application.models.search_visible_episode_segments import (
    RankedTranscriptSegment,
    SearchVisibleEpisodeSegmentsResult,
)
from cinegraph.application.service.grounded_answer_service import (
    GroundedAnswerService,
)
from cinegraph.common.error_messages import GroundedAnswerErrorMessages
from cinegraph.domain.enums.enum import Language, RightsStatus
from cinegraph.domain.models.transcript.transcript_segment import TranscriptSegment
from tests.factories import make_episode_ref


SOURCE_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000401")
SOURCE_VERSION_ID = UUID("00000000-0000-0000-0000-000000000501")


class StubSearchVisibleEpisodeSegmentsService:
    def __init__(self, result: SearchVisibleEpisodeSegmentsResult) -> None:
        self._result = result
        self.queries = []

    def execute(self, query) -> SearchVisibleEpisodeSegmentsResult:
        self.queries.append(query)
        return self._result


class StubChatModelGateway:
    def __init__(self, draft: ModelDraft) -> None:
        self._draft = draft
        self.requests: list[ModelRequest] = []

    def generate_answer(self, request: ModelRequest) -> ModelDraft:
        self.requests.append(request)
        return self._draft


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
        limit=5,
    )


def search_result(
    *matches: TranscriptSegment,
) -> SearchVisibleEpisodeSegmentsResult:
    return SearchVisibleEpisodeSegmentsResult(
        summary=None,
        summary_is_model_context_only=True,
        safe_until_ms=None,
        matches=tuple(
            RankedTranscriptSegment(segment=match, score=1.0) for match in matches
        ),
    )


def test_visible_evidence_is_sole_model_input_and_produces_matched_citations() -> None:
    match = segment(
        segment_id=1,
        start_ms=10_000,
        text="Luke got his head stuck in the banister.",
    )
    search_service = StubSearchVisibleEpisodeSegmentsService(search_result(match))
    gateway = StubChatModelGateway(
        ModelDraft(
            answer="Luke got his head stuck in the banister.",
            cited_segment_ids=(match.segment_id,),
        )
    )
    service = GroundedAnswerService(search_service, gateway)

    result = service.execute(query())

    [request] = gateway.requests
    assert request.question == "Why did Luke get stuck?"
    assert len(request.evidence) == 1
    assert request.evidence[0].segment_id == match.segment_id
    assert request.evidence[0].text == match.text
    assert result.answer == "Luke got his head stuck in the banister."
    assert result.citations == (match,)
    assert result.is_safe_refusal is False


def test_gateway_not_called_when_no_safe_matches_exist() -> None:
    search_service = StubSearchVisibleEpisodeSegmentsService(search_result())
    gateway = StubChatModelGateway(
        ModelDraft(answer="should never be produced", cited_segment_ids=())
    )
    service = GroundedAnswerService(search_service, gateway)

    result = service.execute(query())

    assert gateway.requests == []
    assert result.answer is None
    assert result.citations == ()
    assert result.is_safe_refusal is True


def test_rejects_citation_of_segment_not_passed_as_evidence() -> None:
    match = segment(
        segment_id=1,
        start_ms=10_000,
        text="Luke got his head stuck in the banister.",
    )
    unknown_segment_id = UUID(int=999)
    search_service = StubSearchVisibleEpisodeSegmentsService(search_result(match))
    gateway = StubChatModelGateway(
        ModelDraft(answer="Answer.", cited_segment_ids=(unknown_segment_id,))
    )
    service = GroundedAnswerService(search_service, gateway)

    with pytest.raises(
        ValueError,
        match=GroundedAnswerErrorMessages.MODEL_DRAFT_CANNOT_CITE_UNKNOWN_SEGMENT,
    ):
        service.execute(query())


def test_rejects_duplicate_citation_of_same_segment() -> None:
    match = segment(
        segment_id=1,
        start_ms=10_000,
        text="Luke got his head stuck in the banister.",
    )
    search_service = StubSearchVisibleEpisodeSegmentsService(search_result(match))
    gateway = StubChatModelGateway(
        ModelDraft(
            answer="Answer.",
            cited_segment_ids=(match.segment_id, match.segment_id),
        )
    )
    service = GroundedAnswerService(search_service, gateway)

    with pytest.raises(
        ValueError,
        match=GroundedAnswerErrorMessages.MODEL_DRAFT_CANNOT_CITE_DUPLICATE_SEGMENT,
    ):
        service.execute(query())
