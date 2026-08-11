from cinegraph.application.models.search_visible_episode_segments import (
    RankedTranscriptSegment,
)
from cinegraph.application.models.search_visible_season_segments import (
    SearchVisibleSeasonSegmentsQuery,
    SearchVisibleSeasonSegmentsResult,
)
from cinegraph.common.error_messages import RetrievalErrorMessages
from cinegraph.domain.retrieval.lexical import lexical_score
from cinegraph.domain.policy.spoiler_policy import SpoilerPolicy
from cinegraph.ports.repository.season_episode_catalog import (
    SeasonEpisodeCatalog,
)
from cinegraph.ports.subtitle_processing.transcript_segment_reader import (
    TranscriptSegmentReader,
)


class SearchVisibleSeasonSegmentsService:
    # Initializes the object with its required state.
    def __init__(
        self,
        catalogue: SeasonEpisodeCatalog,
        transcript_reader: TranscriptSegmentReader,
        spoiler_policy: SpoilerPolicy,
    ) -> None:
        self._catalogue = catalogue
        self._transcript_reader = transcript_reader
        self._spoiler_policy = spoiler_policy

    # Executes the operation and returns its result.
    def execute(
        self,
        query: SearchVisibleSeasonSegmentsQuery,
    ) -> SearchVisibleSeasonSegmentsResult:

        # 1. Validate caller-controlled ranking limits.
        if query.limit < 1:
            raise ValueError(
                RetrievalErrorMessages.SEARCH_LIMIT_MUST_BE_POSITIVE
            )

        # 2. Load canonical episode candidates for the requested season.
        episode_refs = self._catalogue.get_episode_refs(
            query.series_id,
            query.season_id,
        )
        if episode_refs is None:
            return SearchVisibleSeasonSegmentsResult(matches=())

        # 3. Load only reviewed transcript segments from visible episodes.
        visible_segments = []
        for episode_ref in episode_refs:
            if self._spoiler_policy.can_access(
                evidence_episode_refs=(episode_ref,),
                watch_state=query.profile_watch_state,
            ):
                visible_segments.extend(
                    self._transcript_reader.get_active_reviewed_segments(
                        episode_ref
                    )
                )
                continue

            safe_until_ms = self._spoiler_policy.partial_safe_until_ms_for(
                episode_ref,
                query.profile_watch_state,
            )
            if safe_until_ms is None:
                continue

            visible_segments.extend(
                segment
                for segment in self._transcript_reader.get_active_reviewed_segments(
                    episode_ref
                )
                if segment.end_ms <= safe_until_ms
            )

        # 4. Rank only spoiler-safe transcript segments.
        ranked_segments = (
            RankedTranscriptSegment(
                segment=segment,
                score=lexical_score(query.query, segment),
            )
            for segment in visible_segments
        )

        # 5. Keep matches and return deterministic top results.
        matches = tuple(
            sorted(
                (
                    ranked_segment
                    for ranked_segment in ranked_segments
                    if ranked_segment.score > 0.0
                ),
                key=lambda ranked_segment: (
                    -ranked_segment.score,
                    ranked_segment.segment.episode.position,
                    ranked_segment.segment.start_ms,
                    str(ranked_segment.segment.segment_id),
                ),
            )[:query.limit]
        )

        return SearchVisibleSeasonSegmentsResult(matches=matches)
