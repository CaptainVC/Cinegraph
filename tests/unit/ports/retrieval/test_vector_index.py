from uuid import UUID

import pytest

from cinegraph.common.error_messages import RetrievalErrorMessages
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.retrieval.retrieval_scope import (
    EpisodeVisibilityScope,
    RetrievalScope,
)
from tests.factories import make_episode_ref


SERIES_ID = UUID("00000000-0000-0000-0000-000000000011")
OTHER_SERIES_ID = UUID("00000000-0000-0000-0000-000000000012")


def episode(episode_id: int, series_id: UUID = SERIES_ID):
    return make_episode_ref(
        series_id=series_id,
        episode_id=UUID(int=episode_id),
        episode_number=episode_id,
    )


def test_non_negative_cutoff_is_accepted() -> None:
    scope = EpisodeVisibilityScope(episode(1), safe_until_ms=0)

    assert scope.safe_until_ms == 0


def test_negative_cutoff_is_rejected_with_central_message() -> None:
    with pytest.raises(
        InvalidModelError,
        match=RetrievalErrorMessages.EPISODE_VISIBILITY_SCOPE_SAFE_UNTIL_MS_MUST_BE_NON_NEGATIVE,
    ):
        EpisodeVisibilityScope(episode(1), safe_until_ms=-1)


def test_duplicate_episode_ids_are_rejected_with_central_message() -> None:
    episode_scope = EpisodeVisibilityScope(episode(1), safe_until_ms=None)

    with pytest.raises(
        InvalidModelError,
        match=RetrievalErrorMessages.RETRIEVAL_SCOPE_CANNOT_HAVE_DUPLICATE_EPISODES,
    ):
        RetrievalScope(SERIES_ID, (episode_scope, episode_scope))


def test_mixed_series_scope_is_rejected_with_central_message() -> None:
    with pytest.raises(
        InvalidModelError,
        match=RetrievalErrorMessages.RETRIEVAL_SCOPE_EPISODES_MUST_MATCH_SERIES,
    ):
        RetrievalScope(
            SERIES_ID,
            (EpisodeVisibilityScope(episode(1, OTHER_SERIES_ID), None),),
        )


def test_list_episode_scopes_are_rejected_with_central_message() -> None:
    with pytest.raises(
        InvalidModelError,
        match=RetrievalErrorMessages.RETRIEVAL_SCOPE_EPISODE_SCOPES_MUST_BE_IMMUTABLE,
    ):
        RetrievalScope(
            SERIES_ID,
            [EpisodeVisibilityScope(episode(1), None)],
        )
