from dataclasses import dataclass
from uuid import UUID

from cinegraph.common.error_messages import RetrievalErrorMessages
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef


@dataclass(frozen=True, slots=True)
class EpisodeVisibilityScope:
    episode: EpisodeRef
    safe_until_ms: int | None

    # Require a non-negative partial-visibility cutoff when one is present.
    def __post_init__(self) -> None:
        if self.safe_until_ms is not None and self.safe_until_ms < 0:
            raise InvalidModelError(
                RetrievalErrorMessages.EPISODE_VISIBILITY_SCOPE_SAFE_UNTIL_MS_MUST_BE_NON_NEGATIVE
            )


@dataclass(frozen=True, slots=True)
class RetrievalScope:
    series_id: UUID
    episode_scopes: tuple[EpisodeVisibilityScope, ...]

    # Enforce immutable, unique episode scopes whose episodes belong to this series.
    def __post_init__(self) -> None:
        if not isinstance(self.episode_scopes, tuple):
            raise InvalidModelError(
                RetrievalErrorMessages.RETRIEVAL_SCOPE_EPISODE_SCOPES_MUST_BE_IMMUTABLE
            )

        episode_ids = set()
        for episode_scope in self.episode_scopes:
            if episode_scope.episode.series_id != self.series_id:
                raise InvalidModelError(
                    RetrievalErrorMessages.RETRIEVAL_SCOPE_EPISODES_MUST_MATCH_SERIES
                )
            if episode_scope.episode.episode_id in episode_ids:
                raise InvalidModelError(
                    RetrievalErrorMessages.RETRIEVAL_SCOPE_CANNOT_HAVE_DUPLICATE_EPISODES
                )
            episode_ids.add(episode_scope.episode.episode_id)
