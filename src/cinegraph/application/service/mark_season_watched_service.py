from cinegraph.application.exceptions.errors import (
    ProfileWatchStateNotFoundError,
    SeasonNotFoundError,
)
from cinegraph.application.models.mark_season_watched import (
    MarkSeasonWatchedCommand,
    MarkSeasonWatchedResult,
)
from cinegraph.application.service.watch_event_factory import create_watch_events
from cinegraph.domain.enums.enum import WatchEventKind
from cinegraph.ports.date_time.clock import Clock
from cinegraph.ports.repository.season_episode_catalog import SeasonEpisodeCatalog
from cinegraph.ports.repository.watch_progress_repository import WatchProgressRepository


class MarkSeasonWatchedService:
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

    # Mark every unwatched episode in a season and persist one event per change.
    def execute(
        self,
        command: MarkSeasonWatchedCommand,
    ) -> MarkSeasonWatchedResult:
        current_state = self._repository.get(command.profile_id)
        if current_state is None:
            raise ProfileWatchStateNotFoundError(command.profile_id)

        season_episodes = self._season_episode_catalog.get_episode_refs(
            command.series_id,
            command.season_id,
        )
        if season_episodes is None:
            raise SeasonNotFoundError(command.series_id, command.season_id)

        episodes_to_watch = tuple(
            episode
            for episode in season_episodes
            if not current_state.is_episode_fully_watched(episode)
        )
        if not episodes_to_watch:
            return MarkSeasonWatchedResult(
                watch_state=current_state,
                watch_events=(),
                was_already_watched=True,
            )

        updated_state = current_state.mark_season_watched(episodes_to_watch)
        watch_events = create_watch_events(
            profile_id=command.profile_id,
            episodes=episodes_to_watch,
            kind=WatchEventKind.EPISODE_MARKED_WATCHED,
            source=command.source,
            occurred_at=self._clock.now_utc(),
        )
        self._repository.persist_state_change(
            watch_state=updated_state,
            watch_events=watch_events,
            expected_version=current_state.version,
        )

        return MarkSeasonWatchedResult(
            watch_state=updated_state,
            watch_events=watch_events,
            was_already_watched=False,
        )
