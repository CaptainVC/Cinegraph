from cinegraph.application.exceptions.errors import ProfileWatchStateNotFoundError
from cinegraph.application.models.mark_episode_unwatched import (
    MarkEpisodeUnwatchedCommand,
    MarkEpisodeUnwatchedResult,
)
from cinegraph.application.service.watch_event_factory import create_watch_events
from cinegraph.domain.enums.enum import WatchEventKind
from cinegraph.ports.date_time.clock import Clock
from cinegraph.ports.repository.watch_progress_repository import WatchProgressRepository


class MarkEpisodeUnwatchedService:
    # Store the watch-progress repository and clock used for unwatched events.
    def __init__(
        self,
        repository: WatchProgressRepository,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._clock = clock

    # Remove one episode's progress, persist its event, or return an idempotent no-op.
    def execute(
        self,
        command: MarkEpisodeUnwatchedCommand,
    ) -> MarkEpisodeUnwatchedResult:
        current_state = self._repository.get(command.profile_id)
        if current_state is None:
            raise ProfileWatchStateNotFoundError(command.profile_id)

        if not current_state.has_episode_progress(command.episode):
            return MarkEpisodeUnwatchedResult(
                watch_state=current_state,
                watch_event=None,
                was_already_unwatched=True,
            )

        updated_state = current_state.mark_episode_unwatched(command.episode)
        watch_event = create_watch_events(
            profile_id=command.profile_id,
            episodes=(command.episode,),
            kind=WatchEventKind.EPISODE_MARKED_UNWATCHED,
            source=command.source,
            occurred_at=self._clock.now_utc(),
        )[0]
        self._repository.persist_state_change(
            watch_state=updated_state,
            watch_events=(watch_event,),
            expected_version=current_state.version,
        )

        return MarkEpisodeUnwatchedResult(
            watch_state=updated_state,
            watch_event=watch_event,
            was_already_unwatched=False,
        )
