from uuid import UUID

import pytest
from tests.factories import (
    make_authenticated_corpus_access_scope,
    make_episode_ref,
    make_guest_corpus_access_scope,
)

from cinegraph.common.error_messages import RetrievalErrorMessages
from cinegraph.domain.enums.enum import SpoilerMode
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeWatchProgress
from cinegraph.domain.models.watch_state.profile_watch_state import ProfileWatchState
from cinegraph.domain.models.watch_state.series_watch_state import SeriesWatchState
from cinegraph.domain.policy.spoiler_policy import SpoilerPolicy
from cinegraph.domain.retrieval.retrieval_scope import EpisodeVisibilityScope
from cinegraph.domain.retrieval.retrieval_scope_compiler import RetrievalScopeCompiler

SERIES_ID = UUID("00000000-0000-0000-0000-000000000011")
PROFILE_ID = UUID("00000000-0000-0000-0000-000000000001")


def profile_watch_state(
    completed: tuple = (),
    partial: tuple = (),
) -> ProfileWatchState:
    return ProfileWatchState(
        profile_id=PROFILE_ID,
        profile_name="Test profile",
        spoiler_mode=SpoilerMode.STRICT,
        series_watch_states=(
            SeriesWatchState(
                series_id=SERIES_ID,
                episode_progress=(
                    *(EpisodeWatchProgress(episode, is_completed=True) for episode in completed),
                    *(EpisodeWatchProgress(episode, safe_until_ms=cutoff) for episode, cutoff in partial),
                ),
            ),
        ),
    )


def test_compiles_full_partial_and_excluded_episodes_in_candidate_order() -> None:
    fully_watched = make_episode_ref(
        series_id=SERIES_ID,
        episode_id=UUID(int=1),
        episode_number=1,
    )
    partially_watched = make_episode_ref(
        series_id=SERIES_ID,
        episode_id=UUID(int=2),
        episode_number=2,
    )
    unwatched = make_episode_ref(
        series_id=SERIES_ID,
        episode_id=UUID(int=3),
        episode_number=3,
    )

    scope = RetrievalScopeCompiler(SpoilerPolicy()).compile(
        series_id=SERIES_ID,
        candidate_episodes=(fully_watched, partially_watched, unwatched),
        watch_state=profile_watch_state(
            completed=(fully_watched,),
            partial=((partially_watched, 32_000),),
        ),
        corpus_access_scope=make_authenticated_corpus_access_scope(),
    )

    assert scope.episode_scopes == (
        EpisodeVisibilityScope(fully_watched, None),
        EpisodeVisibilityScope(partially_watched, 32_000),
    )


def test_no_watch_state_compiles_to_empty_scope() -> None:
    episode = make_episode_ref(series_id=SERIES_ID)

    scope = RetrievalScopeCompiler(SpoilerPolicy()).compile(
        series_id=SERIES_ID,
        candidate_episodes=(episode,),
        watch_state=None,
        corpus_access_scope=make_authenticated_corpus_access_scope(),
    )

    assert scope.episode_scopes == ()


def test_wrong_series_candidate_raises_central_message() -> None:
    episode = make_episode_ref(
        series_id=UUID("00000000-0000-0000-0000-000000000012"),
    )

    with pytest.raises(
        ValueError,
        match=RetrievalErrorMessages.CANDIDATE_EPISODES_MUST_MATCH_SERIES,
    ):
        RetrievalScopeCompiler(SpoilerPolicy()).compile(
            series_id=SERIES_ID,
            candidate_episodes=(episode,),
            watch_state=None,
            corpus_access_scope=make_authenticated_corpus_access_scope(),
        )


def test_guest_scope_excludes_authenticated_only_season_before_spoiler_scope() -> None:
    season_two = make_episode_ref(season_number=2, episode_id=UUID(int=2))
    season_three = make_episode_ref(season_number=3, episode_id=UUID(int=3))
    relaxed_state = ProfileWatchState(
        profile_id=PROFILE_ID,
        profile_name="Guest",
        spoiler_mode=SpoilerMode.RELAXED,
    )

    scope = RetrievalScopeCompiler(SpoilerPolicy()).compile(
        series_id=SERIES_ID,
        candidate_episodes=(season_two, season_three),
        watch_state=relaxed_state,
        corpus_access_scope=make_guest_corpus_access_scope(),
    )

    assert scope.episode_scopes == (EpisodeVisibilityScope(season_two, None),)
