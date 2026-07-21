from uuid import UUID

import pytest

from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.watch_state.episode_watch_state import (
    EpisodeRef,
    EpisodeWatchProgress,
)
from cinegraph.domain.models.watch_state.profile_watch_state import (
    ProfileWatchState,
)
from cinegraph.domain.models.watch_state.series_watch_state import SeriesWatchState
from tests.factories import make_episode_ref


PROFILE_ID = UUID("00000000-0000-0000-0000-000000000001")
SERIES_ID = UUID("00000000-0000-0000-0000-000000000011")
OTHER_SERIES_ID = UUID("00000000-0000-0000-0000-000000000012")
SEASON_ID = UUID("00000000-0000-0000-0000-000000000101")
OTHER_SEASON_ID = UUID("00000000-0000-0000-0000-000000000102")


def episode(
    *,
    episode_number: int,
    season_id: UUID = SEASON_ID,
    series_id: UUID = SERIES_ID,
) -> EpisodeRef:
    return make_episode_ref(
        series_id=series_id,
        season_id=season_id,
        episode_id=UUID(int=1000 + episode_number),
        episode_number=episode_number,
    )


def profile_with_progress(*progress: EpisodeWatchProgress) -> ProfileWatchState:
    return ProfileWatchState(
        profile_id=PROFILE_ID,
        profile_name="Maya",
        series_watch_states=(
            SeriesWatchState(series_id=SERIES_ID, episode_progress=progress),
        ),
    )


def test_mark_episode_unwatched_removes_completed_progress() -> None:
    target = episode(episode_number=1)
    state = profile_with_progress(EpisodeWatchProgress(target, is_completed=True))

    updated_state = state.mark_episode_unwatched(target)

    assert not updated_state.has_episode_progress(target)
    assert updated_state.version == 1


def test_mark_episode_unwatched_is_idempotent_when_no_progress_exists() -> None:
    target = episode(episode_number=1)
    state = profile_with_progress()

    assert state.mark_episode_unwatched(target) is state


def test_mark_episode_watched_is_idempotent_when_already_completed() -> None:
    target = episode(episode_number=1)
    state = profile_with_progress(EpisodeWatchProgress(target, is_completed=True))

    assert state.mark_episode_watched(target) is state


def test_mark_season_watched_updates_every_episode_once() -> None:
    first_episode = episode(episode_number=1)
    second_episode = episode(episode_number=2)
    state = ProfileWatchState(profile_id=PROFILE_ID, profile_name="Maya")

    updated_state = state.mark_season_watched((first_episode, second_episode))

    assert updated_state.is_episode_fully_watched(first_episode)
    assert updated_state.is_episode_fully_watched(second_episode)
    assert updated_state.version == 1


def test_mark_season_unwatched_removes_all_season_progress_once() -> None:
    first_episode = episode(episode_number=1)
    second_episode = episode(episode_number=2)
    state = profile_with_progress(
        EpisodeWatchProgress(first_episode, is_completed=True),
        EpisodeWatchProgress(second_episode, safe_until_ms=32_000),
    )

    updated_state = state.mark_season_unwatched((first_episode, second_episode))

    assert not updated_state.has_episode_progress(first_episode)
    assert not updated_state.has_episode_progress(second_episode)
    assert updated_state.version == 1


def test_mark_season_rejects_episodes_from_multiple_seasons() -> None:
    state = ProfileWatchState(profile_id=PROFILE_ID, profile_name="Maya")

    with pytest.raises(InvalidModelError):
        state.mark_season_watched(
            (
                episode(episode_number=1),
                episode(episode_number=2, season_id=OTHER_SEASON_ID),
            )
        )


def test_mark_season_rejects_episodes_from_multiple_series() -> None:
    state = ProfileWatchState(profile_id=PROFILE_ID, profile_name="Maya")

    with pytest.raises(InvalidModelError):
        state.mark_season_watched(
            (
                episode(episode_number=1),
                episode(
                    episode_number=2,
                    series_id=OTHER_SERIES_ID,
                    season_id=OTHER_SEASON_ID,
                ),
            )
        )
