from cinegraph.application.models.get_visible_episode_context import (
    GetVisibleEpisodeContextQuery,
)
from cinegraph.application.models.search_visible_episode_segments import (
    RankedTranscriptSegment,
    SearchVisibleEpisodeSegmentsQuery,
    SearchVisibleEpisodeSegmentsResult,
)
from cinegraph.application.service.get_visible_episode_context_service import (
    GetVisibleEpisodeContextService,
)
from cinegraph.common.error_messages import RetrievalErrorMessages
from cinegraph.domain.retrieval.lexical import lexical_score


class SearchVisibleEpisodeSegmentsService:
    # Store the context service that enforces episode and summary visibility.
    def __init__(
        self,
        context_service: GetVisibleEpisodeContextService,
    ) -> None:
        self._context_service = context_service

    # Score visible transcript segments and return deterministic top matches with context metadata.
    def execute(
        self,
        query: SearchVisibleEpisodeSegmentsQuery,
    ) -> SearchVisibleEpisodeSegmentsResult:

        # 1. Validate caller-controlled search limits.
        if query.limit < 1:
            raise ValueError(RetrievalErrorMessages.SEARCH_LIMIT_MUST_BE_POSITIVE)

        # 2. Load summary and transcript context already filtered by visibility.
        context = self._context_service.execute(
            GetVisibleEpisodeContextQuery(
                episode=query.episode,
                summary_source_document_id=query.summary_source_document_id,
                profile_watch_state=query.profile_watch_state,
                corpus_access_scope=query.corpus_access_scope,
            )
        )

        # 3. Score only safe transcript segments.
        ranked_segments = tuple(
            RankedTranscriptSegment(
                segment=segment,
                score=lexical_score(query.query, segment),
            )
            for segment in context.transcript_segments
        )

        # 4. Remove non-matches and produce deterministic ranking.
        matches = tuple(
            sorted(
                (
                    ranked_segment
                    for ranked_segment in ranked_segments
                    if ranked_segment.score > 0.0
                ),
                key=lambda ranked_segment: (
                    -ranked_segment.score,
                    ranked_segment.segment.start_ms,
                    str(ranked_segment.segment.segment_id),
                ),
            )[:query.limit]
        )

        # 5. Preserve context visibility metadata for downstream answer assembly.
        return SearchVisibleEpisodeSegmentsResult(
            summary=context.summary,
            summary_is_model_context_only=context.summary_is_model_context_only,
            safe_until_ms=context.safe_until_ms,
            matches=matches,
        )
