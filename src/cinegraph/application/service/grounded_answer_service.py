from uuid import UUID

from cinegraph.application.models.grounded_answer import (
    GroundedAnswerQuery,
    GroundedAnswerResult,
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
    def __init__(
        self,
        search_service: SearchVisibleEpisodeSegmentsService,
        chat_model_gateway: ChatModelGateway,
    ) -> None:
        self._search_service = search_service
        self._chat_model_gateway = chat_model_gateway

    def execute(self, query: GroundedAnswerQuery) -> GroundedAnswerResult:

        # 1. Map onto the shared visible-segment search contract.
        search_result = self._search_service.execute(
            SearchVisibleEpisodeSegmentsQuery(
                query=query.question,
                episode=query.episode,
                summary_source_document_id=query.summary_source_document_id,
                profile_watch_state=query.profile_watch_state,
                limit=query.limit,
            )
        )

        # 2. Build evidence only from ranked transcript segments actually returned.
        evidence_by_segment_id: dict[UUID, TranscriptSegment] = {
            match.segment.segment_id: match.segment
            for match in search_result.matches
        }

        # 3. No visible matching evidence: refuse deterministically, no gateway call.
        if not evidence_by_segment_id:
            return GroundedAnswerResult(
                answer=None,
                citations=(),
                is_safe_refusal=True,
            )

        # 4. Call the gateway with transcript evidence only; summaries stay context-only.
        draft = self._chat_model_gateway.generate_answer(
            ModelRequest(
                question=query.question,
                evidence=tuple(
                    ModelEvidence(
                        segment_id=segment.segment_id,
                        episode=segment.episode,
                        start_ms=segment.start_ms,
                        end_ms=segment.end_ms,
                        text=segment.text,
                    )
                    for segment in evidence_by_segment_id.values()
                ),
            )
        )

        # 5. Reject drafts citing an unknown or repeated transcript segment.
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
