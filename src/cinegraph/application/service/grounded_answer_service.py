from uuid import UUID

from cinegraph.application.models.grounded_answer import (
    GroundedAnswerQuery,
    GroundedAnswerResult,
    ModelEvidence,
    ModelDraft,
    ModelRequest,
)
from cinegraph.application.models.search_visible_episode_segments import (
    SearchVisibleEpisodeSegmentsQuery,
)
from cinegraph.application.service.search_visible_episode_segments_service import (
    SearchVisibleEpisodeSegmentsService,
)
from cinegraph.common.error_messages import GroundedAnswerErrorMessages
from cinegraph.domain.models.transcript.transcript_segment import TranscriptSegment
from cinegraph.ports.llm.chat_model_gateway import ChatModelGateway


class GroundedAnswerService:
    # Initializes the object with its required state.
    def __init__(
        self,
        search_service: SearchVisibleEpisodeSegmentsService,
        chat_model_gateway: ChatModelGateway,
    ) -> None:
        self._search_service = search_service
        self._chat_model_gateway = chat_model_gateway

    # Processes the supplied retrieve visible segments values.
    def retrieve_visible_segments(
        self, query: GroundedAnswerQuery
    ) -> tuple[TranscriptSegment, ...]:
        search_result = self._search_service.execute(
            SearchVisibleEpisodeSegmentsQuery(
                query=query.question,
                episode=query.episode,
                summary_source_document_id=query.summary_source_document_id,
                profile_watch_state=query.profile_watch_state,
                limit=query.limit,
            )
        )
        return tuple(match.segment for match in search_result.matches)

    # Processes the supplied draft answer values.
    def draft_answer(
        self,
        question: str,
        visible_segments: tuple[TranscriptSegment, ...],
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

    # Validates the supplied data against the domain rules.
    def validate_draft(
        self,
        visible_segments: tuple[TranscriptSegment, ...],
        draft: ModelDraft,
    ) -> GroundedAnswerResult:
        # Index visible evidence so cited identifiers can be checked and resolved.
        evidence_by_segment_id: dict[UUID, TranscriptSegment] = {
            segment.segment_id: segment for segment in visible_segments
        }

        # Reject citations that are unknown or repeated before constructing the result.
        seen_segment_ids: set[UUID] = set()
        for segment_id in draft.cited_segment_ids:
            if segment_id not in evidence_by_segment_id:
                raise ValueError(
                    GroundedAnswerErrorMessages.MODEL_DRAFT_CANNOT_CITE_UNKNOWN_SEGMENT
                )
            if segment_id in seen_segment_ids:
                raise ValueError(
                    GroundedAnswerErrorMessages.MODEL_DRAFT_CANNOT_CITE_DUPLICATE_SEGMENT
                )
            seen_segment_ids.add(segment_id)

        return GroundedAnswerResult(
            answer=draft.answer,
            citations=tuple(
                evidence_by_segment_id[segment_id]
                for segment_id in draft.cited_segment_ids
            ),
            is_safe_refusal=False,
        )

    # Executes the operation and returns its result.
    def execute(self, query: GroundedAnswerQuery) -> GroundedAnswerResult:
        # 1. Retrieve ranked visible transcript segments.
        visible_segments = self.retrieve_visible_segments(query)

        # 2. No visible matching evidence: refuse deterministically, no gateway call.
        if not visible_segments:
            return GroundedAnswerResult(
                answer=None,
                citations=(),
                is_safe_refusal=True,
            )

        # 3. Draft from visible evidence, then validate cited segments.
        draft = self.draft_answer(query.question, visible_segments)
        return self.validate_draft(visible_segments, draft)
