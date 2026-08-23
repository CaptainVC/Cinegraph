from cinegraph.application.exceptions.errors import ProfileWatchStateNotFoundError
from cinegraph.application.models.mark_episode_watched import (
    MarkEpisodeWatchedCommand,
    MarkEpisodeWatchedResult,
)
from cinegraph.application.service.watch_event_factory import create_watch_events
from cinegraph.domain.enums.enum import WatchEventKind
from cinegraph.ports.date_time.clock import Clock
from cinegraph.ports.repository.watch_progress_repository import WatchProgressRepository


class MarkEpisodeWatchedService:

    # Store the watch-progress repository and clock used for watched events.
    def __init__(
            self,
            repository: WatchProgressRepository,
            clock: Clock
    ) -> None:
        self._repository = repository
        self._clock = clock

    # Mark one episode watched, persist its event, or return an idempotent no-op.
    def execute(
            self,
            command: MarkEpisodeWatchedCommand
    ) -> MarkEpisodeWatchedResult:

        current_state = self._repository.get(command.profile_id)

        if current_state is None:
            raise ProfileWatchStateNotFoundError(command.profile_id)

        if current_state.is_episode_fully_watched(command.episode):
            return MarkEpisodeWatchedResult(
                watch_state=current_state,
                watch_event=None,
                was_already_watched=True
            )

        updated_state = current_state.mark_episode_watched(command.episode)

        watch_event = create_watch_events(
            profile_id=command.profile_id,
            episodes=(command.episode,),
            kind=WatchEventKind.EPISODE_MARKED_WATCHED,
            source=command.source,
            occurred_at=self._clock.now_utc(),
        )[0]

        self._repository.persist_state_change(
            watch_state=updated_state,
            watch_events=(watch_event,),
            expected_version=current_state.version,
        )

        return MarkEpisodeWatchedResult(
            watch_state=updated_state,
            watch_event=watch_event,
            was_already_watched=False,
        )
