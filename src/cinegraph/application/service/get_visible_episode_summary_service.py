from cinegraph.application.models.get_visible_episode_summary import (
    GetVisibleEpisodeSummaryQuery,
    GetVisibleEpisodeSummaryResult,
)
from cinegraph.domain.policy.spoiler_policy import SpoilerPolicy
from cinegraph.ports.repository.episode_summary_reader import (
    EpisodeSummaryReader,
)


class GetVisibleEpisodeSummaryService:
    # Initializes the object with its required state.
    def __init__(
        self,
        reader: EpisodeSummaryReader,
        spoiler_policy: SpoilerPolicy,
    ) -> None:
        self._reader = reader
        self._spoiler_policy = spoiler_policy

    # Executes the operation and returns its result.
    def execute(
        self,
        query: GetVisibleEpisodeSummaryQuery,
    ) -> GetVisibleEpisodeSummaryResult:
        # 1. Load the active, reviewed summary.
        summary = self._reader.get_active_reviewed_summary(
            query.source_document_id
        )

        # 2. Hide unavailable or unreviewed summary sources.
        if summary is None:
            return GetVisibleEpisodeSummaryResult(summary=None)

        # 3. Return fully visible summaries for authorized episodes.
        can_access = self._spoiler_policy.can_access(
            evidence_episode_refs=(summary.episode,),
            watch_state=query.profile_watch_state,
        )

        if can_access:
            return GetVisibleEpisodeSummaryResult(summary=summary)

        # 4. Provide partial-watch summaries only as model context.
        safe_until_ms = self._spoiler_policy.partial_safe_until_ms_for(
            summary.episode,
            query.profile_watch_state,
        )
        if safe_until_ms is None:
            return GetVisibleEpisodeSummaryResult(summary=None)

        return GetVisibleEpisodeSummaryResult(
            summary=summary,
            safe_until_ms=safe_until_ms,
            is_model_context_only=True,
        )
