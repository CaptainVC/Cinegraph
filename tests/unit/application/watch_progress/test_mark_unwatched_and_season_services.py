from datetime import UTC, datetime
from uuid import UUID

import pytest

from cinegraph.adapters.repository.in_memory.in_memory_season_episode_catalog import (
    InMemorySeasonEpisodeCatalog,
)
from cinegraph.adapters.repository.in_memory.in_memory_watch_progress_repository import (
    InMemoryWatchProgressRepository,
)
from cinegraph.application.exceptions.errors import SeasonNotFoundError
from cinegraph.application.models.mark_episode_unwatched import (
    MarkEpisodeUnwatchedCommand,
)
from cinegraph.application.models.mark_season_unwatched import (
    MarkSeasonUnwatchedCommand,
)
from cinegraph.application.models.mark_season_watched import MarkSeasonWatchedCommand
from cinegraph.application.service.mark_episode_unwatched_service import (
    MarkEpisodeUnwatchedService,
)
from cinegraph.application.service.mark_season_unwatched_service import (
    MarkSeasonUnwatchedService,
)
from cinegraph.application.service.mark_season_watched_service import (
    MarkSeasonWatchedService,
)
from cinegraph.domain.enums.enum import WatchEventKind
from cinegraph.domain.models.watch_state.episode_watch_state import (
    EpisodeRef,
    EpisodeWatchProgress,
)
from cinegraph.domain.models.watch_state.profile_watch_state import (
    ProfileWatchState,
)
from tests.factories import DEFAULT_FIXED_TIME, FixedClock, make_episode_ref
from cinegraph.domain.models.watch_state.series_watch_state import SeriesWatchState


PROFILE_ID = UUID("00000000-0000-0000-0000-000000000001")
SERIES_ID = UUID("00000000-0000-0000-0000-000000000011")
SEASON_ID = UUID("00000000-0000-0000-0000-000000000101")
MISSING_SEASON_ID = UUID("00000000-0000-0000-0000-000000000199")
FIXED_TIME = DEFAULT_FIXED_TIME


def episode(episode_number: int) -> EpisodeRef:
    return make_episode_ref(
        series_id=SERIES_ID,
        season_id=SEASON_ID,
        episode_id=UUID(int=1000 + episode_number),
        episode_number=episode_number,
    )


def profile_watch_state(*progress: EpisodeWatchProgress) -> ProfileWatchState:
    return ProfileWatchState(
        profile_id=PROFILE_ID,
        profile_name="Maya",
        series_watch_states=(
            SeriesWatchState(series_id=SERIES_ID, episode_progress=progress),
        ),
    )


def season_catalog() -> InMemorySeasonEpisodeCatalog:
    return InMemorySeasonEpisodeCatalog((episode(1), episode(2), episode(3)))


def test_marks_an_episode_unwatched_and_records_one_event() -> None:
    target = episode(1)
    repository = InMemoryWatchProgressRepository(
        [profile_watch_state(EpisodeWatchProgress(target, is_completed=True))]
    )
    service = MarkEpisodeUnwatchedService(repository, FixedClock())

    result = service.execute(
        MarkEpisodeUnwatchedCommand(profile_id=PROFILE_ID, episode=target)
    )

    assert result.was_already_unwatched is False
    assert result.watch_event is not None
    assert result.watch_event.kind is WatchEventKind.EPISODE_MARKED_UNWATCHED
    assert result.watch_event.occurred_at == FIXED_TIME
    assert not result.watch_state.has_episode_progress(target)
    assert result.watch_state.version == 1
    assert repository.watch_events == (result.watch_event,)


def test_repeating_mark_episode_unwatched_is_idempotent() -> None:
    target = episode(1)
    repository = InMemoryWatchProgressRepository(
        [profile_watch_state(EpisodeWatchProgress(target, is_completed=True))]
    )
    service = MarkEpisodeUnwatchedService(repository, FixedClock())
    command = MarkEpisodeUnwatchedCommand(profile_id=PROFILE_ID, episode=target)

    first_result = service.execute(command)
    second_result = service.execute(command)

    assert first_result.watch_event is not None
    assert second_result.was_already_unwatched is True
    assert second_result.watch_event is None
    assert repository.watch_events == (first_result.watch_event,)
    assert repository.get(PROFILE_ID).version == 1


def test_mark_season_watched_completes_only_incomplete_episodes() -> None:
    first_episode = episode(1)
    second_episode = episode(2)
    repository = InMemoryWatchProgressRepository(
        [
            profile_watch_state(
                EpisodeWatchProgress(first_episode, is_completed=True),
                EpisodeWatchProgress(second_episode, safe_until_ms=32_000),
            )
        ]
    )
    service = MarkSeasonWatchedService(repository, season_catalog(), FixedClock())

    result = service.execute(
        MarkSeasonWatchedCommand(
            profile_id=PROFILE_ID,
            series_id=SERIES_ID,
            season_id=SEASON_ID,
        )
    )

    assert result.was_already_watched is False
    assert [event.episode for event in result.watch_events] == [
        second_episode,
        episode(3),
    ]
    assert all(
        event.kind is WatchEventKind.EPISODE_MARKED_WATCHED
        for event in result.watch_events
    )
    assert all(result.watch_state.is_episode_fully_watched(item) for item in season_catalog().get_episode_refs(SERIES_ID, SEASON_ID) or ())
    assert result.watch_state.version == 1
    assert repository.watch_events == result.watch_events


def test_repeating_mark_season_watched_is_idempotent() -> None:
    episodes = season_catalog().get_episode_refs(SERIES_ID, SEASON_ID)
    assert episodes is not None
    repository = InMemoryWatchProgressRepository(
        [
            profile_watch_state(
                *(EpisodeWatchProgress(item, is_completed=True) for item in episodes)
            )
        ]
    )
    service = MarkSeasonWatchedService(repository, season_catalog(), FixedClock())

    result = service.execute(
        MarkSeasonWatchedCommand(
            profile_id=PROFILE_ID,
            series_id=SERIES_ID,
            season_id=SEASON_ID,
        )
    )

    assert result.was_already_watched is True
    assert result.watch_events == ()
    assert repository.watch_events == ()
    assert repository.get(PROFILE_ID).version == 0


def test_mark_season_unwatched_removes_all_progress_and_records_events() -> None:
    episodes = season_catalog().get_episode_refs(SERIES_ID, SEASON_ID)
    assert episodes is not None
    repository = InMemoryWatchProgressRepository(
        [
            profile_watch_state(
                *(EpisodeWatchProgress(item, is_completed=True) for item in episodes)
            )
        ]
    )
    service = MarkSeasonUnwatchedService(repository, season_catalog(), FixedClock())

    result = service.execute(
        MarkSeasonUnwatchedCommand(
            profile_id=PROFILE_ID,
            series_id=SERIES_ID,
            season_id=SEASON_ID,
        )
    )

    assert result.was_already_unwatched is False
    assert [event.episode for event in result.watch_events] == list(episodes)
    assert all(
        event.kind is WatchEventKind.EPISODE_MARKED_UNWATCHED
        for event in result.watch_events
    )
    assert all(not result.watch_state.has_episode_progress(item) for item in episodes)
    assert result.watch_state.version == 1
    assert repository.watch_events == result.watch_events


def test_mark_season_rejects_an_unknown_canonical_season() -> None:
    repository = InMemoryWatchProgressRepository([profile_watch_state()])
    service = MarkSeasonWatchedService(repository, season_catalog(), FixedClock())

    with pytest.raises(SeasonNotFoundError):
        service.execute(
            MarkSeasonWatchedCommand(
                profile_id=PROFILE_ID,
                series_id=SERIES_ID,
                season_id=MISSING_SEASON_ID,
            )
        )

    assert repository.watch_events == ()
