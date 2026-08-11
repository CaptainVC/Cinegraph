from uuid import UUID

from cinegraph.common.error_messages import RetrievalErrorMessages
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef
from cinegraph.domain.models.watch_state.profile_watch_state import ProfileWatchState
from cinegraph.domain.policy.spoiler_policy import SpoilerPolicy
from cinegraph.domain.retrieval.retrieval_scope import (
    EpisodeVisibilityScope,
    RetrievalScope,
)


class RetrievalScopeCompiler:
    # Initializes the object with its required state.
    def __init__(self, spoiler_policy: SpoilerPolicy) -> None:
        self._spoiler_policy = spoiler_policy

    # Compiles the supplied domain scope into the adapter representation.
    def compile(
        self,
        series_id: UUID,
        candidate_episodes: tuple[EpisodeRef, ...],
        watch_state: ProfileWatchState | None,
    ) -> RetrievalScope:
        # Reject candidate episodes that belong to a different series.
        for episode in candidate_episodes:
            if episode.series_id != series_id:
                raise ValueError(
                    RetrievalErrorMessages.CANDIDATE_EPISODES_MUST_MATCH_SERIES
                )

        # Keep fully accessible episodes and bounded partial visibility scopes.
        episode_scopes = []
        for episode in candidate_episodes:
            if self._spoiler_policy.can_access((episode,), watch_state):
                episode_scopes.append(
                    EpisodeVisibilityScope(episode=episode, safe_until_ms=None)
                )
                continue

            safe_until_ms = self._spoiler_policy.partial_safe_until_ms_for(
                episode,
                watch_state,
            )
            if safe_until_ms is not None:
                episode_scopes.append(
                    EpisodeVisibilityScope(
                        episode=episode,
                        safe_until_ms=safe_until_ms,
                    )
                )

        # Return the compiled scope consumed by retrieval adapters.
        return RetrievalScope(
            series_id=series_id,
            episode_scopes=tuple(episode_scopes),
        )
