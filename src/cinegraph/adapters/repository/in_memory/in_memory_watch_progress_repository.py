from typing import Iterable
from uuid import UUID

from cinegraph.common.error_messages import WatchErrorMessages
from cinegraph.domain.enums.enum import WatchEventKind
from cinegraph.domain.models.watch_event.watch_event import WatchEvent
from cinegraph.domain.models.watch_state.profile_watch_state import ProfileWatchState
from cinegraph.ports.errors.error import ConcurrentWatchProgressUpdateError


class InMemoryWatchProgressRepository:

    # Initializes the object with its required state.
    def __init__(
            self,
            initial_watch_states: Iterable[ProfileWatchState] = ()
            ) -> None:

        initial_states = tuple(initial_watch_states)

        self._watch_states = {
            state.profile_id: state
            for state in initial_states
        }
        self._watch_events: list[WatchEvent] = []

        if len(self._watch_states) != len(initial_states):
            raise ValueError(
                WatchErrorMessages.INITIAL_WATCH_STATES_MUST_HAVE_UNIQUE_PROFILE_IDS
            )


    # Gets and returns the requested value.
    def get(self, profile_id: UUID) -> ProfileWatchState | None:
        return self._watch_states.get(profile_id)

    # Persists the supplied value in the repository.
    def persist_state_change(
        self,
        watch_state: ProfileWatchState,
        watch_events: tuple[WatchEvent, ...],
        *,
        expected_version: int,
    ) -> None:
        current_state = self._watch_states.get(watch_state.profile_id)

        if current_state is None:
            raise KeyError(
                WatchErrorMessages.NO_WATCH_STATE_FOUND_FOR_PROFILE_ID.format(
                    profile_id=watch_state.profile_id
                )
            )

        if current_state.version != expected_version:
            raise ConcurrentWatchProgressUpdateError(
                WatchErrorMessages.CONCURRENT_WATCH_PROGRESS
            )

        if watch_state.version != expected_version + 1:
            raise ValueError(
                WatchErrorMessages.EXPECTED_VERSION_MISMATCH
            )

        for watch_event in watch_events:
            if watch_event.profile_id != watch_state.profile_id:
                raise ValueError(WatchErrorMessages.WATCH_EVENT_PROFILE_MISMATCH)

            if (
                watch_event.kind is WatchEventKind.EPISODE_MARKED_WATCHED
                and not watch_state.is_episode_fully_watched(watch_event.episode)
            ):
                raise ValueError(WatchErrorMessages.WATCH_EVENT_NOT_FULLY_WATCHED)

            if (
                watch_event.kind is WatchEventKind.EPISODE_MARKED_UNWATCHED
                and watch_state.has_episode_progress(watch_event.episode)
            ):
                raise ValueError(
                    WatchErrorMessages.UNWATCHED_EVENT_REQUIRES_NO_PROGRESS
                )

        self._watch_states[watch_state.profile_id] = watch_state
        self._watch_events.extend(watch_events)

    @property
    # Processes the supplied watch events values.
    def watch_events(self) -> tuple[WatchEvent, ...]:
        return tuple(self._watch_events)
