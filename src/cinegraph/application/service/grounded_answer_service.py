from uuid import UUID

from cinegraph.application.models.grounded_answer import (
    GroundedAnswerQuery,
    GroundedAnswerResult,
    ModelDraft,
    ModelEvidence,
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
    # Store the visible-segment search service and structured answer gateway.
    def __init__(
        self,
        search_service: SearchVisibleEpisodeSegmentsService,
        chat_model_gateway: ChatModelGateway,
    ) -> None:
        self._search_service = search_service
        self._chat_model_gateway = chat_model_gateway

    # Retrieve ranked transcript segments already filtered by episode visibility.
    def retrieve_visible_segments(
        self, query: GroundedAnswerQuery
    ) -> tuple[TranscriptSegment, ...]:
        search_result = self._search_service.execute(
            SearchVisibleEpisodeSegmentsQuery(
                query=query.question,
                episode=query.episode,
                summary_source_document_id=query.summary_source_document_id,
                profile_watch_state=query.profile_watch_state,
                corpus_access_scope=query.corpus_access_scope,
                limit=query.limit,
            )
        )
        return tuple(match.segment for match in search_result.matches)

    # Build model evidence from visible segments and request a structured draft.
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

    # Reject unknown or duplicate citations and construct the grounded result.
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

    # Refuse without a model call when no evidence is visible; otherwise draft and validate.
    def execute(self, query: GroundedAnswerQuery) -> GroundedAnswerResult:
        # Retrieve ranked transcript segments already filtered by spoiler visibility.
        visible_segments = self.retrieve_visible_segments(query)

        # Refuse deterministically when retrieval returns no visible evidence.
        if not visible_segments:
            return GroundedAnswerResult(
                answer=None,
                citations=(),
                is_safe_refusal=True,
            )

        # Draft from visible evidence and validate every cited segment identifier.
        draft = self.draft_answer(query.question, visible_segments)
        return self.validate_draft(visible_segments, draft)
