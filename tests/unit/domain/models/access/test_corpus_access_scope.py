from uuid import UUID

import pytest

from cinegraph.common.error_messages import AccessErrorMessages
from cinegraph.config import (
    DEFAULT_GUEST_ACCESS_CONFIGURATION,
    DEFAULT_GUEST_CORPUS_ACCESS_SCOPE,
)
from cinegraph.domain.enums.enum import CorpusAccessMode
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.access import CorpusAccessScope, CorpusSeasonAccess
from tests.factories import make_authenticated_corpus_access_scope, make_episode_ref


def test_default_guest_scope_allows_only_modern_family_seasons_one_and_two() -> None:
    configuration = DEFAULT_GUEST_ACCESS_CONFIGURATION

    assert DEFAULT_GUEST_CORPUS_ACCESS_SCOPE.allows_episode(
        make_episode_ref(series_id=configuration.series_id, season_number=1)
    )
    assert DEFAULT_GUEST_CORPUS_ACCESS_SCOPE.allows_episode(
        make_episode_ref(series_id=configuration.series_id, season_number=2)
    )
    assert not DEFAULT_GUEST_CORPUS_ACCESS_SCOPE.allows_episode(
        make_episode_ref(series_id=configuration.series_id, season_number=3)
    )
    assert not DEFAULT_GUEST_CORPUS_ACCESS_SCOPE.allows_episode(
        make_episode_ref(series_id=UUID(int=999), season_number=1)
    )


def test_authenticated_unrestricted_scope_allows_future_corpus() -> None:
    scope = make_authenticated_corpus_access_scope()

    assert scope.allows_episode(
        make_episode_ref(series_id=UUID(int=999), season_number=12)
    )


def test_guest_scope_cannot_be_unrestricted() -> None:
    with pytest.raises(
        InvalidModelError,
        match=AccessErrorMessages.GUEST_CORPUS_SCOPE_CANNOT_BE_UNRESTRICTED,
    ):
        CorpusAccessScope(
            mode=CorpusAccessMode.GUEST,
            revision="invalid-guest-v1",
            allowed_seasons=frozenset({CorpusSeasonAccess(UUID(int=1), 1)}),
            unrestricted=True,
        )


def test_guest_scope_requires_at_least_one_explicit_season() -> None:
    with pytest.raises(
        InvalidModelError,
        match=AccessErrorMessages.GUEST_CORPUS_SCOPE_REQUIRES_ALLOWED_SEASONS,
    ):
        CorpusAccessScope(
            mode=CorpusAccessMode.GUEST,
            revision="empty-guest-v1",
            allowed_seasons=frozenset(),
        )


def test_allowed_seasons_must_be_immutable() -> None:
    with pytest.raises(
        InvalidModelError,
        match=AccessErrorMessages.CORPUS_SCOPE_ALLOWED_SEASONS_MUST_BE_IMMUTABLE,
    ):
        CorpusAccessScope(
            mode=CorpusAccessMode.AUTHENTICATED,
            revision="mutable-scope-v1",
            allowed_seasons=set(),  # type: ignore[arg-type]
        )


def test_invalid_mode_cannot_bypass_guest_unrestricted_invariant() -> None:
    with pytest.raises(
        InvalidModelError,
        match=AccessErrorMessages.CORPUS_ACCESS_MODE_MUST_BE_VALID,
    ):
        CorpusAccessScope(
            mode="authenticated",  # type: ignore[arg-type]
            revision="forged-mode-v1",
            allowed_seasons=frozenset(),
            unrestricted=True,
        )
