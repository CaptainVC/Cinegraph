from uuid import UUID

import pytest

from cinegraph.application.models.grounded_answer import ModelDraft
from cinegraph.application.models.hybrid_grounded_answer import (
    HybridGroundedAnswerQuery,
)
from cinegraph.application.models.search_visible_hybrid_segments import (
    SearchVisibleHybridSegmentsResult,
)
from cinegraph.application.service.hybrid_grounded_answer_service import (
    HybridGroundedAnswerService,
)
from cinegraph.domain.enums.enum import Language, RightsStatus
from cinegraph.ports.retrieval import RetrievedSegment
from tests.factories import make_authenticated_corpus_access_scope, make_episode_ref


class RecordingSearchService:
    def __init__(self, matches: tuple[RetrievedSegment, ...]) -> None:
        self.matches = matches
        self.queries = []

    def execute(self, query):
        self.queries.append(query)
        return SearchVisibleHybridSegmentsResult(
            matches=self.matches,
            visible_episode_count=1,
        )


class DraftGateway:
    def __init__(self, draft: ModelDraft) -> None:
        self.draft = draft
        self.requests = []

    def generate_answer(self, request):
        self.requests.append(request)
        return self.draft


def make_match(segment_id: UUID = UUID(int=101)) -> RetrievedSegment:
    return RetrievedSegment(
        segment_id=segment_id,
        source_version_id=UUID(int=201),
        episode=make_episode_ref(),
        start_ms=1_000,
        end_ms=2_000,
        text="Phil describes the family photo.",
        language=Language.ENGLISH,
        rights_status=RightsStatus.ALLOWED,
        score=0.91,
    )


def make_query() -> HybridGroundedAnswerQuery:
    episode = make_episode_ref()
    return HybridGroundedAnswerQuery(
        question="What does Phil describe?",
        series_id=episode.series_id,
        candidate_episodes=(episode,),
        profile_watch_state=None,
        corpus_access_scope=make_authenticated_corpus_access_scope(),
    )


def test_retrieval_preserves_trusted_scope_and_maps_provenance_to_model_evidence() -> None:
    match = make_match()
    search = RecordingSearchService((match,))
    gateway = DraftGateway(
        ModelDraft(answer="Phil describes the photo.", cited_segment_ids=(match.segment_id,))
    )
    service = HybridGroundedAnswerService(search, gateway)

    visible = service.retrieve_visible_segments(make_query())
    draft = service.draft_answer(make_query().question, visible)
    result = service.validate_draft(visible, draft)

    assert search.queries[0].corpus_access_scope == make_query().corpus_access_scope
    assert gateway.requests[0].evidence[0].segment_id == match.segment_id
    assert result.answer == "Phil describes the photo."
    assert result.citations == (match,)
    assert result.is_safe_refusal is False


@pytest.mark.parametrize(
    "draft",
    [
        ModelDraft(answer="Invented.", cited_segment_ids=(UUID(int=999),)),
        ModelDraft(answer="Repeated.", cited_segment_ids=(UUID(int=101), UUID(int=101))),
        ModelDraft(answer="Uncited.", cited_segment_ids=()),
        ModelDraft(answer=None, cited_segment_ids=(UUID(int=101),)),
    ],
)
def test_malformed_or_ungrounded_model_drafts_fail_closed(draft: ModelDraft) -> None:
    service = HybridGroundedAnswerService(
        RecordingSearchService(()),
        DraftGateway(draft),
    )

    with pytest.raises(ValueError):
        service.validate_draft((make_match(),), draft)


def test_explicit_model_refusal_is_safe_and_has_no_citations() -> None:
    service = HybridGroundedAnswerService(
        RecordingSearchService(()),
        DraftGateway(ModelDraft(answer=None, cited_segment_ids=())),
    )

    result = service.validate_draft(
        (make_match(),),
        ModelDraft(answer=None, cited_segment_ids=()),
    )

    assert result.answer is None
    assert result.citations == ()
    assert result.is_safe_refusal is True
