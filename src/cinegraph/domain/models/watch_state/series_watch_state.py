from dataclasses import dataclass, field, replace
from uuid import UUID

from cinegraph.common.error_messages import WatchErrorMessages
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.watch_state.episode_watch_state import (
    EpisodeRef,
    EpisodeWatchProgress,
)


@dataclass(frozen=True, slots=True)
class SeriesWatchState:
    series_id: UUID
    episode_progress: tuple[EpisodeWatchProgress, ...] = field(default_factory=tuple)
    manually_allowed_episodes: frozenset[EpisodeRef] = field(default_factory=frozenset)
    sequential_safe_boundary: EpisodeRef | None = None

    # Enforce immutable progress collections, series ownership, and unique episodes.
    def __post_init__(self) -> None:
        if not isinstance(self.episode_progress, tuple):
            raise InvalidModelError(
                WatchErrorMessages.EPISODE_PROGRESS_MUST_BE_IMMUTABLE
            )
        if not isinstance(self.manually_allowed_episodes, frozenset):
            raise InvalidModelError(
                WatchErrorMessages.MANUALLY_ALLOWED_EPISODES_MUST_BE_IMMUTABLE
            )
        episode_refs = {progress.episode for progress in self.episode_progress}
        if self.sequential_safe_boundary is not None:
            episode_refs = episode_refs.union({self.sequential_safe_boundary})
        episode_refs = episode_refs.union(self.manually_allowed_episodes)
        if any(
            episode_ref.series_id != self.series_id
            for episode_ref in episode_refs
        ):
            raise InvalidModelError(
                WatchErrorMessages.SERIES_WATCH_STATE_EPISODES_MUST_MATCH_SERIES
            )
        progress_episode_ids = {
            progress.episode.episode_id for progress in self.episode_progress
        }
        if len(progress_episode_ids) != len(self.episode_progress):
            raise InvalidModelError(
                WatchErrorMessages.SERIES_WATCH_STATE_CANNOT_HAVE_DUPLICATE_PROGRESS
            )

    # Return the progress record for an episode, or None when it is absent.
    def progress_for(self, episode: EpisodeRef) -> EpisodeWatchProgress | None:
        return next(
            (
                progress
                for progress in self.episode_progress
                if progress.episode == episode
            ),
            None,
        )

    # Return whether the episode has a completed progress record.
    def is_fully_watched(self, episode: EpisodeRef) -> bool:
        progress = self.progress_for(episode)
        return progress is not None and progress.is_completed

    # Return whether any progress record exists for the episode.
    def has_progress_for(self, episode: EpisodeRef) -> bool:
        return self.progress_for(episode) is not None

    # Return the partial-watch cutoff, excluding completed or unknown episodes.
    def safe_until_ms_for(self, episode: EpisodeRef) -> int | None:
        progress = self.progress_for(episode)
        if progress is None or progress.is_completed:
            return None
        return progress.safe_until_ms

    # Replace an episode's progress with completed progress, preserving no-op identity.
    def mark_episode_watched(self, episode: EpisodeRef) -> "SeriesWatchState":
        # Confirm ownership, then replace any prior progress with completed progress.
        self._validate_episode_series(episode)

        if self.is_fully_watched(episode):
            return self

        updated_progress = tuple(
            progress
            for progress in self.episode_progress
            if progress.episode.episode_id != episode.episode_id
        ) + (
            EpisodeWatchProgress(
                episode=episode,
                is_completed=True,
            ),
        )

        # Return the immutable series state with the updated progress tuple.
        return replace(self, episode_progress=updated_progress)

    # Remove an episode's progress after validating that it belongs to this series.
    def mark_episode_unwatched(self, episode: EpisodeRef) -> "SeriesWatchState":
        # Confirm ownership before removing the episode's progress.
        self._validate_episode_series(episode)

        if not self.has_progress_for(episode):
            return self

        updated_progress = tuple(
            progress
            for progress in self.episode_progress
            if progress.episode.episode_id != episode.episode_id
        )
        return replace(self, episode_progress=updated_progress)

    # Validate and fold watched updates for multiple episodes into one immutable state.
    def mark_episodes_watched(
        self,
        episodes: tuple[EpisodeRef, ...],
    ) -> "SeriesWatchState":
        # Validate every episode before applying the updates sequentially.
        for episode in episodes:
            self._validate_episode_series(episode)

        # Fold each episode update into the immutable state returned by the prior step.
        updated_state = self
        for episode in episodes:
            updated_state = updated_state.mark_episode_watched(episode)
        return updated_state

    # Validate ownership and remove progress for all requested episodes.
    def mark_episodes_unwatched(
        self,
        episodes: tuple[EpisodeRef, ...],
    ) -> "SeriesWatchState":
        # Validate ownership, then remove all requested episode identifiers together.
        for episode in episodes:
            self._validate_episode_series(episode)

        episode_ids = {episode.episode_id for episode in episodes}
        updated_progress = tuple(
            progress
            for progress in self.episode_progress
            if progress.episode.episode_id not in episode_ids
        )
        if updated_progress == self.episode_progress:
            return self
        return replace(self, episode_progress=updated_progress)

    # Reject an episode that belongs to a different series watch state.
    def _validate_episode_series(self, episode: EpisodeRef) -> None:
        if episode.series_id != self.series_id:
            raise InvalidModelError(
                WatchErrorMessages.EPISODE_MUST_MATCH_SERIES_WATCH_STATE
            )
