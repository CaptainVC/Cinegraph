from dataclasses import dataclass, replace
from uuid import UUID

from cinegraph.common.error_messages import WatchErrorMessages
from cinegraph.domain.enums.enum import SpoilerMode
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef
from cinegraph.domain.models.watch_state.series_watch_state import SeriesWatchState

@dataclass(frozen=True, slots=True)
class ProfileWatchState:

    profile_id: UUID
    profile_name: str
    series_watch_states: tuple[SeriesWatchState, ...] = ()
    spoiler_mode: SpoilerMode = SpoilerMode.STRICT
    version: int = 0

    def __post_init__(self) -> None:
        if not self.profile_id:
            raise InvalidModelError(WatchErrorMessages.PROFILE_ID_CANNOT_BE_EMPTY)

        if not self.profile_name or self.profile_name.strip() != self.profile_name:
            raise InvalidModelError(WatchErrorMessages.PROFILE_NAME_MUST_BE_TRIMMED)

        if not isinstance(self.series_watch_states, tuple):
            raise InvalidModelError(
                WatchErrorMessages.SERIES_WATCH_STATES_MUST_BE_IMMUTABLE
            )

        series_ids = {state.series_id for state in self.series_watch_states}
        if len(series_ids) != len(self.series_watch_states):
            raise InvalidModelError(
                WatchErrorMessages.PROFILE_CANNOT_HAVE_DUPLICATE_SERIES_WATCH_STATES
            )

        if self.version < 0:
            raise InvalidModelError(
                WatchErrorMessages.PROFILE_WATCH_STATE_VERSION_CANNOT_BE_NEGATIVE
            )

    def series_watch_state_for(self, series_id: UUID) -> SeriesWatchState | None:
        return next(
            (
                state
                for state in self.series_watch_states
                if state.series_id == series_id
            ),
            None,
        )

    def is_episode_fully_watched(self, episode: EpisodeRef) -> bool:
        series_state = self.series_watch_state_for(episode.series_id)
        return series_state.is_fully_watched(episode) if series_state else False

    def has_episode_progress(self, episode: EpisodeRef) -> bool:
        series_state = self.series_watch_state_for(episode.series_id)
        return series_state.has_progress_for(episode) if series_state else False

    def mark_episode_watched(self, episode: EpisodeRef) -> "ProfileWatchState":
        existing_series_state = self.series_watch_state_for(episode.series_id)

        if existing_series_state is None:
            updated_series_state = SeriesWatchState(
                series_id=episode.series_id,
            ).mark_episode_watched(episode)

            updated_series_states = (
                *self.series_watch_states,
                updated_series_state,
            )
        else:
            updated_series_state = existing_series_state.mark_episode_watched(episode)
            if updated_series_state is existing_series_state:
                return self

            # Replace the existing series state with the updated one
            # Keep the order and rest of the series state unchanged
            updated_series_states = tuple(
                updated_series_state
                if state.series_id == episode.series_id else state
                for state in self.series_watch_states
            )

        return replace(
                self,
            series_watch_states=updated_series_states,
            version=self.version + 1,
        )

    def mark_episode_unwatched(self, episode: EpisodeRef) -> "ProfileWatchState":
        existing_series_state = self.series_watch_state_for(episode.series_id)
        if existing_series_state is None:
            return self

        updated_series_state = existing_series_state.mark_episode_unwatched(episode)
        if updated_series_state is existing_series_state:
            return self

        return replace(
            self,
            series_watch_states=tuple(
                updated_series_state if state.series_id == episode.series_id else state
                for state in self.series_watch_states
            ),
            version=self.version + 1,
        )

    def mark_season_watched(
        self,
        episodes: tuple[EpisodeRef, ...],
    ) -> "ProfileWatchState":
        series_id = self._validate_season_episodes(episodes)
        existing_series_state = self.series_watch_state_for(series_id)
        if existing_series_state is None:
            updated_series_state = SeriesWatchState(
                series_id=series_id,
            ).mark_episodes_watched(episodes)
            return replace(
                self,
                series_watch_states=(*self.series_watch_states, updated_series_state),
                version=self.version + 1,
            )

        updated_series_state = existing_series_state.mark_episodes_watched(episodes)
        if updated_series_state is existing_series_state:
            return self

        return replace(
            self,
            series_watch_states=tuple(
                updated_series_state if state.series_id == series_id else state
                for state in self.series_watch_states
            ),
            version=self.version + 1,
        )

    def mark_season_unwatched(
        self,
        episodes: tuple[EpisodeRef, ...],
    ) -> "ProfileWatchState":
        series_id = self._validate_season_episodes(episodes)
        existing_series_state = self.series_watch_state_for(series_id)
        if existing_series_state is None:
            return self

        updated_series_state = existing_series_state.mark_episodes_unwatched(episodes)
        if updated_series_state is existing_series_state:
            return self

        return replace(
            self,
            series_watch_states=tuple(
                updated_series_state if state.series_id == series_id else state
                for state in self.series_watch_states
            ),
            version=self.version + 1,
        )

    def _validate_season_episodes(self, episodes: tuple[EpisodeRef, ...]) -> UUID:
        if not episodes:
            raise InvalidModelError(
                WatchErrorMessages.SEASON_OPERATION_REQUIRES_EPISODES
            )

        series_ids = {episode.series_id for episode in episodes}
        if len(series_ids) != 1:
            raise InvalidModelError(
                WatchErrorMessages.SEASON_OPERATION_EPISODES_MUST_SHARE_SERIES
            )

        season_ids = {episode.season_id for episode in episodes}
        if len(season_ids) != 1:
            raise InvalidModelError(
                WatchErrorMessages.SEASON_OPERATION_EPISODES_MUST_SHARE_SEASON
            )

        return episodes[0].series_id
