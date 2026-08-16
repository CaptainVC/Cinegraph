from uuid import UUID

from cinegraph.application.models.grounded_answer import (
    ModelDraft,
    ModelEvidence,
    ModelRequest,
)
from cinegraph.application.models.hybrid_grounded_answer import (
    HybridGroundedAnswerQuery,
    HybridGroundedAnswerResult,
)
from cinegraph.application.models.search_visible_hybrid_segments import (
    SearchVisibleHybridSegmentsQuery,
)
from cinegraph.application.service.search_visible_hybrid_segments_service import (
    SearchVisibleHybridSegmentsService,
)
from cinegraph.common.error_messages import GroundedAnswerErrorMessages
from cinegraph.ports.llm.chat_model_gateway import ChatModelGateway
from cinegraph.ports.retrieval import RetrievedSegment


class HybridGroundedAnswerService:
    def __init__(
        self,
        search_service: SearchVisibleHybridSegmentsService,
        chat_model_gateway: ChatModelGateway,
    ) -> None:
        self._search_service = search_service
        self._chat_model_gateway = chat_model_gateway

    def retrieve_visible_segments(
        self,
        query: HybridGroundedAnswerQuery,
    ) -> tuple[RetrievedSegment, ...]:
        return self._search_service.execute(
            SearchVisibleHybridSegmentsQuery(
                query=query.question,
                series_id=query.series_id,
                candidate_episodes=query.candidate_episodes,
                profile_watch_state=query.profile_watch_state,
                corpus_access_scope=query.corpus_access_scope,
                limit=query.limit,
            )
        ).matches

    def draft_answer(
        self,
        question: str,
        visible_segments: tuple[RetrievedSegment, ...],
    ) -> ModelDraft:
        return self._chat_model_gateway.generate_answer(
            ModelRequest(
                question=question,
                evidence=tuple(
                    ModelEvidence(
                        segment_id=segment.segment_id,
                        episode=segment.episode,
                        start_ms=segment.start_ms,
                        end_ms=segment.end_ms,
                        text=segment.text,
                    )
                    for segment in visible_segments
                ),
            )
        )

    def validate_draft(
        self,
        visible_segments: tuple[RetrievedSegment, ...],
        draft: ModelDraft,
    ) -> HybridGroundedAnswerResult:
        evidence_by_id: dict[UUID, RetrievedSegment] = {
            segment.segment_id: segment for segment in visible_segments
        }
        seen_ids: set[UUID] = set()
        for segment_id in draft.cited_segment_ids:
            if segment_id not in evidence_by_id:
                raise ValueError(
                    GroundedAnswerErrorMessages.MODEL_DRAFT_CANNOT_CITE_UNKNOWN_SEGMENT
                )
            if segment_id in seen_ids:
                raise ValueError(
                    GroundedAnswerErrorMessages.MODEL_DRAFT_CANNOT_CITE_DUPLICATE_SEGMENT
                )
            seen_ids.add(segment_id)

        if draft.answer is None:
            if draft.cited_segment_ids:
                raise ValueError("A refusal cannot cite transcript evidence.")
        elif (
            not isinstance(draft.answer, str)
            or not draft.answer
            or draft.answer.strip() != draft.answer
            or not draft.cited_segment_ids
        ):
            raise ValueError("A grounded answer must be trimmed and cite evidence.")

        return HybridGroundedAnswerResult(
            answer=draft.answer,
            citations=tuple(
                evidence_by_id[segment_id] for segment_id in draft.cited_segment_ids
            ),
            is_safe_refusal=draft.answer is None,
        )
