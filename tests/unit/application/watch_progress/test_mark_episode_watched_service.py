from datetime import UTC
from uuid import UUID

import pytest

from cinegraph.adapters.date_time.system_clock import SystemClock
from cinegraph.adapters.repository.in_memory.in_memory_watch_progress_repository import (
    InMemoryWatchProgressRepository,
)
from cinegraph.application.exceptions.errors import ProfileWatchStateNotFoundError
from cinegraph.application.models.mark_episode_watched import (
    MarkEpisodeWatchedCommand,
)
from cinegraph.application.service.mark_episode_watched_service import (
    MarkEpisodeWatchedService,
)
from cinegraph.domain.enums.enum import WatchEventSource
from cinegraph.domain.models.watch_state.episode_watch_state import (
    EpisodeRef,
    EpisodeWatchProgress,
)
from cinegraph.domain.models.watch_state.profile_watch_state import (
    ProfileWatchState,
)
from tests.factories import DEFAULT_FIXED_TIME, FixedClock, make_episode_ref
from cinegraph.domain.models.watch_state.series_watch_state import (
    SeriesWatchState,
)


PROFILE_ID = UUID("00000000-0000-0000-0000-000000000001")
SERIES_ID = UUID("00000000-0000-0000-0000-000000000011")
SEASON_ID = UUID("00000000-0000-0000-0000-000000000101")
EPISODE_ID = UUID("00000000-0000-0000-0000-000000001001")
MISSING_PROFILE_ID = UUID("00000000-0000-0000-0000-000000009999")
FIXED_TIME = DEFAULT_FIXED_TIME


def episode() -> EpisodeRef:
    return make_episode_ref(
        series_id=SERIES_ID,
        season_id=SEASON_ID,
        episode_id=EPISODE_ID,
    )


def profile_watch_state(
    *,
    progress: EpisodeWatchProgress | None = None,
) -> ProfileWatchState:
    series_states = ()
    if progress is not None:
        series_states = (
            SeriesWatchState(
                series_id=SERIES_ID,
                episode_progress=(progress,),
            ),
        )

    return ProfileWatchState(
        profile_id=PROFILE_ID,
        profile_name="Maya",
        series_watch_states=series_states,
    )


def test_marks_an_unwatched_episode_and_records_one_event() -> None:
    repository = InMemoryWatchProgressRepository([profile_watch_state()])
    service = MarkEpisodeWatchedService(repository, FixedClock())

    result = service.execute(
        MarkEpisodeWatchedCommand(
            profile_id=PROFILE_ID,
            episode=episode(),
        )
    )

    assert result.was_already_watched is False
    assert result.watch_event is not None
    assert result.watch_event.source is WatchEventSource.MANUAL
    assert result.watch_event.occurred_at == FIXED_TIME
    assert result.watch_state.version == 1
    assert repository.get(PROFILE_ID) == result.watch_state
    assert result.watch_state.is_episode_fully_watched(episode())
    assert repository.watch_events == (result.watch_event,)


def test_repeating_mark_watched_is_idempotent() -> None:
    repository = InMemoryWatchProgressRepository([profile_watch_state()])
    service = MarkEpisodeWatchedService(repository, FixedClock())
    command = MarkEpisodeWatchedCommand(profile_id=PROFILE_ID, episode=episode())

    first_result = service.execute(command)
    second_result = service.execute(command)

    assert first_result.watch_event is not None
    assert second_result.was_already_watched is True
    assert second_result.watch_event is None
    assert repository.watch_events == (first_result.watch_event,)
    assert repository.get(PROFILE_ID).version == 1


def test_marking_a_partial_episode_as_watched_removes_the_cutoff() -> None:
    partial_progress = EpisodeWatchProgress(episode(), safe_until_ms=32_000)
    repository = InMemoryWatchProgressRepository(
        [profile_watch_state(progress=partial_progress)]
    )
    service = MarkEpisodeWatchedService(repository, FixedClock())

    service.execute(
        MarkEpisodeWatchedCommand(profile_id=PROFILE_ID, episode=episode())
    )

    stored_state = repository.get(PROFILE_ID)
    assert stored_state is not None
    series_state = stored_state.series_watch_state_for(SERIES_ID)
    assert series_state is not None
    progress = series_state.progress_for(episode())
    assert progress is not None
    assert progress.is_completed is True
    assert progress.safe_until_ms is None


def test_missing_profile_state_does_not_record_an_event() -> None:
    repository = InMemoryWatchProgressRepository()
    service = MarkEpisodeWatchedService(repository, FixedClock())

    with pytest.raises(ProfileWatchStateNotFoundError):
        service.execute(
            MarkEpisodeWatchedCommand(
                profile_id=MISSING_PROFILE_ID,
                episode=episode(),
            )
        )

    assert repository.watch_events == ()


def test_system_clock_returns_an_aware_utc_timestamp() -> None:
    timestamp = SystemClock().now_utc()

    assert timestamp.tzinfo is UTC