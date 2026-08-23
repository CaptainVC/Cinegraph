from uuid import UUID

from tests.factories import make_authenticated_corpus_access_scope, make_episode_ref

from cinegraph.adapters.workflow.langgraph.hybrid_grounded_answer_graph import (
    HybridGroundedAnswerGraphWorkflow,
)
from cinegraph.application.models.grounded_answer import ModelDraft
from cinegraph.application.models.hybrid_grounded_answer import (
    HybridGroundedAnswerQuery,
    HybridGroundedAnswerResult,
)
from cinegraph.config.transcript_chunking import TRANSCRIPT_INDEX_REVISION
from cinegraph.domain.enums.enum import Language, RightsStatus
from cinegraph.ports.retrieval import RetrievedSegment


class RecordingService:
    def __init__(self, matches, drafts=(), outcomes=()) -> None:
        self.matches = matches
        self.drafts = list(drafts)
        self.outcomes = list(outcomes)
        self.calls = []

    def retrieve_visible_segments(self, query):
        self.calls.append("retrieve")
        return self.matches

    def draft_answer(self, question, matches):
        self.calls.append("draft")
        return self.drafts.pop(0)

    def validate_draft(self, matches, draft):
        self.calls.append("verify")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, ValueError):
            raise outcome
        return outcome


def make_match() -> RetrievedSegment:
    return RetrievedSegment(
        segment_id=UUID(int=101),
        source_version_id=UUID(int=201),
        episode=make_episode_ref(),
        start_ms=1_000,
        end_ms=2_000,
        text="Phil describes the family photo.",
        language=Language.ENGLISH,
        rights_status=RightsStatus.ALLOWED,
        score=0.91,
        member_segment_ids=(UUID(int=101),),
        index_revision=TRANSCRIPT_INDEX_REVISION,
        ordinal=0,
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


def test_no_visible_evidence_refuses_before_any_model_call() -> None:
    service = RecordingService(())

    result = HybridGroundedAnswerGraphWorkflow(service).execute(make_query())

    assert result == HybridGroundedAnswerResult(None, (), True)
    assert service.calls == ["retrieve"]


def test_invalid_first_draft_is_retried_and_verified() -> None:
    match = make_match()
    valid = HybridGroundedAnswerResult("A family photo.", (match,), False)
    service = RecordingService(
        (match,),
        drafts=(
            ModelDraft("Invented.", (UUID(int=999),)),
            ModelDraft("A family photo.", (match.segment_id,)),
        ),
        outcomes=(ValueError("bad citation"), valid),
    )

    result = HybridGroundedAnswerGraphWorkflow(service).execute(make_query())

    assert result == valid
    assert service.calls == ["retrieve", "draft", "verify", "draft", "verify"]


def test_exhausted_citation_retries_fail_closed() -> None:
    match = make_match()
    service = RecordingService(
        (match,),
        drafts=(
            ModelDraft("Invented.", (UUID(int=999),)),
            ModelDraft("Still invented.", (UUID(int=998),)),
        ),
        outcomes=(ValueError("bad"), ValueError("bad again")),
    )

    result = HybridGroundedAnswerGraphWorkflow(service).execute(make_query())

    assert result == HybridGroundedAnswerResult(None, (), True)
