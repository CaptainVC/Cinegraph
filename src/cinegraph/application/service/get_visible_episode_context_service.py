from cinegraph.application.models.get_visible_episode_context import (
    GetVisibleEpisodeContextQuery,
    GetVisibleEpisodeContextResult,
)
from cinegraph.application.models.get_visible_episode_summary import (
    GetVisibleEpisodeSummaryQuery,
)
from cinegraph.application.service.get_visible_episode_summary_service import (
    GetVisibleEpisodeSummaryService,
)
from cinegraph.domain.policy.spoiler_policy import SpoilerPolicy
from cinegraph.ports.subtitle_processing.transcript_segment_reader import (
    TranscriptSegmentReader,
)


class GetVisibleEpisodeContextService:
    # Store summary, transcript, and spoiler-policy services for context assembly.
    def __init__(
        self,
        summary_service: GetVisibleEpisodeSummaryService,
        transcript_reader: TranscriptSegmentReader,
        spoiler_policy: SpoilerPolicy,
    ) -> None:
        self._summary_service = summary_service
        self._transcript_reader = transcript_reader
        self._spoiler_policy = spoiler_policy

    # Combine visible summary and transcript context, applying a partial-watch cutoff when needed.
    def execute(
        self,
        query: GetVisibleEpisodeContextQuery,
    ) -> GetVisibleEpisodeContextResult:
        # Reject corpus-ineligible episodes before touching either private source port.
        if not query.corpus_access_scope.allows_episode(query.episode):
            return GetVisibleEpisodeContextResult(
                summary=None,
                transcript_segments=(),
                safe_until_ms=None,
                summary_is_model_context_only=False,
            )

        # Resolve summary visibility for this profile.
        summary_result = self._summary_service.execute(
            GetVisibleEpisodeSummaryQuery(
                source_document_id=query.summary_source_document_id,
                profile_watch_state=query.profile_watch_state,
                corpus_access_scope=query.corpus_access_scope,
            )
        )

        # Load approved transcript segments before applying episode visibility.
        segments = self._transcript_reader.get_active_reviewed_segments(
            query.episode
        )

        # Fully accessible episodes expose every approved segment.
        if self._spoiler_policy.can_access(
            evidence_episode_refs=(query.episode,),
            watch_state=query.profile_watch_state,
        ):
            return GetVisibleEpisodeContextResult(
                summary=summary_result.summary,
                transcript_segments=segments,
                safe_until_ms=None,
                summary_is_model_context_only=False,
            )

        # Partial watches expose only segments ending at or before the safe cutoff.
        safe_until_ms = self._spoiler_policy.partial_safe_until_ms_for(
            query.episode,
            query.profile_watch_state,
        )
        if safe_until_ms is None:
            return GetVisibleEpisodeContextResult(
                summary=None,
                transcript_segments=(),
                safe_until_ms=None,
                summary_is_model_context_only=False,
            )

        visible_segments = tuple(
            segment
            for segment in segments
            if segment.end_ms <= safe_until_ms
        )

        # Keep partially visible summaries available only to downstream model context.
        return GetVisibleEpisodeContextResult(
            summary=summary_result.summary,
            transcript_segments=visible_segments,
            safe_until_ms=safe_until_ms,
            summary_is_model_context_only=(
                summary_result.is_model_context_only
            ),
        )
