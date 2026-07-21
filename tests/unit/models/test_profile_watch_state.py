from uuid import UUID

import pytest

from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.catalogue.episode import Episode
from cinegraph.domain.models.catalogue.season import Season
from cinegraph.domain.models.catalogue.series import Series
from cinegraph.domain.models.watch_state.episode_watch_state import (
    EpisodePosition,
    EpisodeRef,
    EpisodeWatchProgress,
)
from cinegraph.domain.models.watch_state.profile_watch_state import (
    ProfileWatchState,
)
from cinegraph.domain.models.watch_state.series_watch_state import SeriesWatchState
from tests.factories import make_episode_ref


SERIES_ONE_ID = UUID("00000000-0000-0000-0000-000000000001")
SERIES_TWO_ID = UUID("00000000-0000-0000-0000-000000000002")
SEASON_ONE_ID = UUID("00000000-0000-0000-0000-000000000101")
SEASON_TWO_ID = UUID("00000000-0000-0000-0000-000000000102")
EPISODE_ONE_ID = UUID("00000000-0000-0000-0000-000000001001")
EPISODE_TWO_ID = UUID("00000000-0000-0000-0000-000000001002")


def episode_ref(
    series_id: UUID = SERIES_ONE_ID,
    season_id: UUID = SEASON_ONE_ID,
    episode_id: UUID = EPISODE_ONE_ID,
    season_number: int = 1,
    episode_number: int = 1,
) -> EpisodeRef:
    return make_episode_ref(
        series_id=series_id,
        season_id=season_id,
        episode_id=episode_id,
        season_number=season_number,
        episode_number=episode_number,
    )


def test_episode_position_orders_by_season_then_episode() -> None:
    assert EpisodePosition(1, 24) < EpisodePosition(2, 1)


def test_partial_watch_requires_safe_playback_position() -> None:
    with pytest.raises(InvalidModelError):
        EpisodeWatchProgress(episode=episode_ref())


def test_completed_watch_cannot_have_partial_cutoff() -> None:
    with pytest.raises(InvalidModelError):
        EpisodeWatchProgress(
            episode=episode_ref(),
            is_completed=True,
            safe_until_ms=32_000,
        )


def test_series_watch_state_rejects_duplicate_episode_progress() -> None:
    watched_episode = episode_ref()

    with pytest.raises(InvalidModelError):
        SeriesWatchState(
            series_id=SERIES_ONE_ID,
            episode_progress=(
                EpisodeWatchProgress(watched_episode, is_completed=True),
                EpisodeWatchProgress(watched_episode, safe_until_ms=32_000),
            ),
        )


def test_series_watch_state_rejects_foreign_episode() -> None:
    foreign_episode = episode_ref(
        series_id=SERIES_TWO_ID,
        episode_id=EPISODE_TWO_ID,
    )

    with pytest.raises(InvalidModelError):
        SeriesWatchState(
            series_id=SERIES_ONE_ID,
            manually_allowed_episodes=frozenset({foreign_episode}),
        )


def test_series_watch_state_rejects_mutable_collections() -> None:
    with pytest.raises(InvalidModelError):
        SeriesWatchState(
            series_id=SERIES_ONE_ID,
            episode_progress=[],
        )


def test_series_watch_state_exposes_partial_watch_cutoff() -> None:
    partially_watched_episode = episode_ref()
    watch_state = SeriesWatchState(
        series_id=SERIES_ONE_ID,
        episode_progress=(
            EpisodeWatchProgress(partially_watched_episode, safe_until_ms=32_000),
        ),
    )

    assert watch_state.safe_until_ms_for(partially_watched_episode) == 32_000
    assert not watch_state.is_fully_watched(partially_watched_episode)


def test_catalogue_entities_enforce_containment_and_immutability() -> None:
    episode = Episode(
        series_id=SERIES_ONE_ID,
        season_id=SEASON_ONE_ID,
        episode_id=EPISODE_ONE_ID,
        episode_number=1,
    )
    season = Season(
        series_id=SERIES_ONE_ID,
        season_id=SEASON_ONE_ID,
        season_number=1,
        episodes=(episode,),
    )
    series = Series(
        series_id=SERIES_ONE_ID,
        series_name="Example Series",
        seasons=(season,),
    )

    assert series.seasons == (season,)


def test_catalogue_rejects_episode_from_another_season() -> None:
    foreign_episode = Episode(
        series_id=SERIES_ONE_ID,
        season_id=SEASON_TWO_ID,
        episode_id=EPISODE_TWO_ID,
        episode_number=1,
    )

    with pytest.raises(InvalidModelError):
        Season(
            series_id=SERIES_ONE_ID,
            season_id=SEASON_ONE_ID,
            season_number=1,
            episodes=(foreign_episode,),
        )


def test_profile_rejects_duplicate_series_watch_states() -> None:
    series_watch_state = SeriesWatchState(series_id=SERIES_ONE_ID)

    with pytest.raises(InvalidModelError):
        ProfileWatchState(
            profile_id=UUID("00000000-0000-0000-0000-000000009999"),
            profile_name="Test profile",
            series_watch_states=(series_watch_state, series_watch_state),
        )


def test_profile_rejects_mutable_series_watch_states() -> None:
    with pytest.raises(InvalidModelError):
        ProfileWatchState(
            profile_id=UUID("00000000-0000-0000-0000-000000009999"),
            profile_name="Test profile",
            series_watch_states=[],
        )
