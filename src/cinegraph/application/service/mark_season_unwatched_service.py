from cinegraph.application.exceptions.errors import (
    ProfileWatchStateNotFoundError,
    SeasonNotFoundError,
)
from cinegraph.application.models.mark_season_unwatched import (
    MarkSeasonUnwatchedCommand,
    MarkSeasonUnwatchedResult,
)
from cinegraph.application.service.watch_event_factory import create_watch_events
from cinegraph.domain.enums.enum import WatchEventKind
from cinegraph.ports.date_time.clock import Clock
from cinegraph.ports.repository.season_episode_catalog import SeasonEpisodeCatalog
from cinegraph.ports.repository.watch_progress_repository import WatchProgressRepository


class MarkSeasonUnwatchedService:
    # Store the progress repository, season catalogue, and event clock.
    def __init__(
        self,
        repository: WatchProgressRepository,
        season_episode_catalog: SeasonEpisodeCatalog,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._season_episode_catalog = season_episode_catalog
        self._clock = clock

    # Remove progress from every progressed episode in a season and persist its events.
    def execute(
        self,
        command: MarkSeasonUnwatchedCommand,
    ) -> MarkSeasonUnwatchedResult:
        current_state = self._repository.get(command.profile_id)
        if current_state is None:
            raise ProfileWatchStateNotFoundError(command.profile_id)

        season_episodes = self._season_episode_catalog.get_episode_refs(
            command.series_id,
            command.season_id,
        )
        if season_episodes is None:
            raise SeasonNotFoundError(command.series_id, command.season_id)

        episodes_to_unwatch = tuple(
            episode
            for episode in season_episodes
            if current_state.has_episode_progress(episode)
        )
        if not episodes_to_unwatch:
            return MarkSeasonUnwatchedResult(
                watch_state=current_state,
                watch_events=(),
                was_already_unwatched=True,
            )

        updated_state = current_state.mark_season_unwatched(episodes_to_unwatch)
        watch_events = create_watch_events(
            profile_id=command.profile_id,
            episodes=episodes_to_unwatch,
            kind=WatchEventKind.EPISODE_MARKED_UNWATCHED,
            source=command.source,
            occurred_at=self._clock.now_utc(),
        )
        self._repository.persist_state_change(
            watch_state=updated_state,
            watch_events=watch_events,
            expected_version=current_state.version,
        )

        return MarkSeasonUnwatchedResult(
            watch_state=updated_state,
            watch_events=watch_events,
            was_already_unwatched=False,
        )
