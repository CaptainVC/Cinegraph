from collections.abc import Collection
from uuid import UUID

import pytest
from tests.factories import make_episode_ref

from cinegraph.domain.enums.enum import SpoilerMode
from cinegraph.domain.models.watch_state.episode_watch_state import (
    EpisodeRef,
    EpisodeWatchProgress,
)
from cinegraph.domain.models.watch_state.profile_watch_state import (
    ProfileWatchState,
)
from cinegraph.domain.models.watch_state.series_watch_state import SeriesWatchState
from cinegraph.domain.policy.spoiler_policy import SpoilerPolicy

PROFILE_ID = UUID("00000000-0000-0000-0000-000000000001")
SERIES_ONE_ID = UUID("00000000-0000-0000-0000-000000000011")
SERIES_TWO_ID = UUID("00000000-0000-0000-0000-000000000012")
SEASON_ONE_ID = UUID("00000000-0000-0000-0000-000000000101")
SEASON_TWO_ID = UUID("00000000-0000-0000-0000-000000000102")


def episode_ref(
        series_id: UUID,
        season_id: UUID,
        episode_number: int,
        season_number: int = 1,
) -> EpisodeRef:
    return make_episode_ref(
        series_id=series_id,
        season_id=season_id,
        episode_id=UUID(int=episode_number + season_number * 100),
        season_number=season_number,
        episode_number=episode_number,
    )

episode_1_1 = episode_ref(SERIES_ONE_ID, SEASON_ONE_ID, 1)
episode_1_2 = episode_ref(SERIES_ONE_ID, SEASON_ONE_ID, 2)
episode_1_3 = episode_ref(SERIES_ONE_ID, SEASON_ONE_ID, 3)
episode_2_1 = episode_ref(SERIES_ONE_ID, SEASON_TWO_ID, 1, season_number=2)
different_series_episode = episode_ref(SERIES_TWO_ID, SEASON_ONE_ID, 1)


def profile_watch_state(
        *series_states: SeriesWatchState,
        spoiler_mode: SpoilerMode = SpoilerMode.STRICT,
) -> ProfileWatchState:
    return ProfileWatchState(
        profile_id=PROFILE_ID,
        profile_name="Test profile",
        series_watch_states=series_states,
        spoiler_mode=spoiler_mode,
    )


def series_watch_state(
        series_id: UUID,
        completed: tuple[EpisodeRef, ...] = (),
        partial: tuple[tuple[EpisodeRef, int], ...] = (),
        manually_allowed: frozenset[EpisodeRef] = frozenset(),
        boundary: EpisodeRef | None = None,
) -> SeriesWatchState:
    return SeriesWatchState(
        series_id=series_id,
        episode_progress=tuple(
            EpisodeWatchProgress(episode, is_completed=True)
            for episode in completed
        ) + tuple(
            EpisodeWatchProgress(episode, safe_until_ms=safe_until_ms)
            for episode, safe_until_ms in partial
        ),
        manually_allowed_episodes=manually_allowed,
        sequential_safe_boundary=boundary,
    )

TEST_CASES = [
    pytest.param(
        profile_watch_state(),
        [],
        frozenset(),
        False,
        id="blocks_empty_evidence",
    ),
    pytest.param(
        profile_watch_state(),
        [episode_1_1],
        frozenset(),
        False,
        id="strict_blocks_unwatched_evidence",
    ),
    pytest.param(
        profile_watch_state(
            series_watch_state(SERIES_ONE_ID, completed=(episode_1_1,)),
        ),
        [episode_1_1],
        frozenset({episode_1_1}),
        True,
        id="strict_allows_watched_evidence",
    ),
    pytest.param(
        profile_watch_state(
            series_watch_state(SERIES_ONE_ID, completed=(episode_1_1,)),
        ),
        [episode_1_2],
        frozenset(),
        False,
        id="strict_blocks_different_episode",
    ),
    pytest.param(
        profile_watch_state(
            series_watch_state(
                SERIES_ONE_ID,
                completed=(episode_1_1, episode_1_2),
            ),
        ),
        [episode_1_1, episode_1_2],
        frozenset({episode_1_1, episode_1_2}),
        True,
        id="strict_allows_all_watched_evidence",
    ),
    pytest.param(
        profile_watch_state(
            series_watch_state(
                SERIES_ONE_ID,
                completed=(episode_1_1, episode_1_2),
            ),
        ),
        [episode_1_1, episode_1_3],
        frozenset({episode_1_1}),
        False,
        id="strict_blocks_mixed_evidence",
    ),
    pytest.param(
        profile_watch_state(
            series_watch_state(
                SERIES_ONE_ID,
                completed=(episode_1_1, episode_1_3),
            ),
        ),
        [episode_1_2],
        frozenset(),
        False,
        id="strict_blocks_skipped_episode",
    ),
    pytest.param(
        profile_watch_state(
            series_watch_state(
                SERIES_ONE_ID,
                manually_allowed=frozenset({episode_1_1}),
            ),
        ),
        [episode_1_1],
        frozenset({episode_1_1}),
        True,
        id="manual_allow_overrides_strict_mode",
    ),
    pytest.param(
        profile_watch_state(
            series_watch_state(SERIES_ONE_ID, boundary=episode_1_3),
            spoiler_mode=SpoilerMode.SEQUENTIAL,
        ),
        [episode_1_2],
        frozenset({episode_1_2}),
        True,
        id="sequential_allows_episode_before_boundary",
    ),
    pytest.param(
        profile_watch_state(
            series_watch_state(SERIES_ONE_ID, boundary=episode_1_2),
            spoiler_mode=SpoilerMode.SEQUENTIAL,
        ),
        [episode_1_3],
        frozenset(),
        False,
        id="sequential_blocks_episode_after_boundary",
    ),
    pytest.param(
        profile_watch_state(
            series_watch_state(SERIES_ONE_ID, boundary=episode_1_1),
            spoiler_mode=SpoilerMode.SEQUENTIAL,
        ),
        [different_series_episode],
        frozenset(),
        False,
        id="sequential_boundary_does_not_cross_series",
    ),
    pytest.param(
        profile_watch_state(
            series_watch_state(SERIES_ONE_ID, completed=(episode_1_1,)),
            spoiler_mode=SpoilerMode.RELAXED,
        ),
        [episode_1_2, episode_1_3],
        frozenset({episode_1_2, episode_1_3}),
        True,
        id="relaxed_allows_any_episode",
    ),
    pytest.param(
        None,
        [episode_1_1],
        frozenset(),
        False,
        id="blocks_missing_watch_state",
    ),
    pytest.param(
        profile_watch_state(
            series_watch_state(
                SERIES_ONE_ID,
                completed=(episode_1_1, episode_1_2),
            ),
        ),
        [episode_1_1],
        frozenset({episode_1_1}),
        True,
        id="strict_allows_watched_evidence_from_mixed_availability",
    ),
    pytest.param(
        profile_watch_state(
            series_watch_state(
                SERIES_ONE_ID,
                completed=(episode_1_1, episode_1_2),
            ),
        ),
        [episode_1_3],
        frozenset(),
        False,
        id="strict_blocks_unwatched_evidence_from_mixed_availability",
    ),
    pytest.param(
        profile_watch_state(
            series_watch_state(SERIES_ONE_ID, boundary=episode_1_3),
            spoiler_mode=SpoilerMode.STRICT,
        ),
        [episode_1_2],
        frozenset(),
        False,
        id="strict_ignores_sequential_boundary",
    ),
    pytest.param(
        profile_watch_state(
            series_watch_state(SERIES_ONE_ID, boundary=episode_1_2),
            spoiler_mode=SpoilerMode.SEQUENTIAL,
        ),
        [episode_1_2],
        frozenset({episode_1_2}),
        True,
        id="sequential_allows_episode_at_boundary",
    ),
    pytest.param(
        profile_watch_state(
            series_watch_state(SERIES_ONE_ID),
            spoiler_mode=SpoilerMode.SEQUENTIAL,
        ),
        [episode_1_1, episode_1_2],
        frozenset(),
        False,
        id="sequential_blocks_unwatched_evidence_without_boundary",
    ),
    pytest.param(
        profile_watch_state(
            series_watch_state(
                SERIES_ONE_ID,
                manually_allowed=frozenset({episode_1_3}),
            ),
            spoiler_mode=SpoilerMode.SEQUENTIAL,
        ),
        [episode_1_3],
        frozenset({episode_1_3}),
        True,
        id="manual_allow_overrides_missing_sequential_boundary",
    ),
    pytest.param(
        profile_watch_state(
            series_watch_state(SERIES_ONE_ID, boundary=episode_1_2),
            series_watch_state(SERIES_TWO_ID, boundary=different_series_episode),
            spoiler_mode=SpoilerMode.SEQUENTIAL,
        ),
        [episode_1_2, different_series_episode],
        frozenset({episode_1_2, different_series_episode}),
        True,
        id="sequential_applies_each_series_boundary_independently",
    ),
    pytest.param(
        profile_watch_state(
            series_watch_state(SERIES_ONE_ID, boundary=episode_2_1),
            spoiler_mode=SpoilerMode.SEQUENTIAL,
        ),
        [episode_1_3],
        frozenset({episode_1_3}),
        True,
        id="sequential_allows_episode_in_previous_season",
    ),
    pytest.param(
        profile_watch_state(spoiler_mode=SpoilerMode.RELAXED),
        [],
        frozenset(),
        False,
        id="relaxed_still_blocks_empty_evidence",
    ),
    pytest.param(
        profile_watch_state(
            spoiler_mode=SpoilerMode.RELAXED,
        ),
        [different_series_episode],
        frozenset({different_series_episode}),
        True,
        id="relaxed_allows_evidence_from_another_series",
    ),
    pytest.param(
        profile_watch_state(
            series_watch_state(SERIES_ONE_ID, completed=(episode_1_1,)),
        ),
        [episode_1_1, episode_1_1],
        frozenset({episode_1_1}),
        True,
        id="deduplicates_repeated_watched_evidence",
    ),
]


@pytest.mark.parametrize(
    (
        "watch_state",
        "candidate_episode_ids",
        "expected_accessible_episode_ids",
        "expected_can_access"
    ),
    TEST_CASES,
)
def test_spoiler_policy(
    watch_state: ProfileWatchState | None,
    candidate_episode_ids: Collection[EpisodeRef],
    expected_accessible_episode_ids: frozenset[EpisodeRef],
    expected_can_access: bool,
) -> None:

    assert SpoilerPolicy().accessible_episode_refs(
        evidence_episode_refs=candidate_episode_ids,
        watch_state=watch_state,
    ) == expected_accessible_episode_ids

    assert SpoilerPolicy().can_access(
        evidence_episode_refs=candidate_episode_ids,
        watch_state=watch_state,
    ) is expected_can_access


def test_partial_watch_does_not_allow_the_whole_episode() -> None:
    watch_state = profile_watch_state(
        series_watch_state(
            SERIES_ONE_ID,
            partial=((episode_1_2, 32_000),),
        ),
    )
    policy = SpoilerPolicy()

    assert not policy.can_access(
        evidence_episode_refs=[episode_1_2],
        watch_state=watch_state,
    )
    assert policy.partial_safe_until_ms_for(episode_1_2, watch_state) == 32_000


def test_partial_watch_is_superseded_by_manual_episode_access() -> None:
    watch_state = profile_watch_state(
        series_watch_state(
            SERIES_ONE_ID,
            partial=((episode_1_2, 32_000),),
            manually_allowed=frozenset({episode_1_2}),
        ),
    )
    policy = SpoilerPolicy()

    assert policy.can_access(
        evidence_episode_refs=[episode_1_2],
        watch_state=watch_state,
    )
    assert policy.partial_safe_until_ms_for(episode_1_2, watch_state) is None
