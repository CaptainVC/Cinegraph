from datetime import UTC, datetime
from uuid import UUID

from cinegraph.config import DEFAULT_GUEST_CORPUS_ACCESS_SCOPE
from cinegraph.domain.enums.enum import CorpusAccessMode
from cinegraph.domain.models.access import CorpusAccessScope
from cinegraph.domain.models.watch_state import EpisodePosition, EpisodeRef

DEFAULT_SERIES_ID = UUID("00000000-0000-0000-0000-000000000011")
DEFAULT_SEASON_ID = UUID("00000000-0000-0000-0000-000000000101")
DEFAULT_EPISODE_ID = UUID("00000000-0000-0000-0000-000000001001")
DEFAULT_FIXED_TIME = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


class FixedClock:
    def __init__(self, value: datetime = DEFAULT_FIXED_TIME) -> None:
        self._value = value

    def now_utc(self) -> datetime:
        return self._value


def make_authenticated_corpus_access_scope(
    *,
    revision: str = "authenticated-test-v1",
) -> CorpusAccessScope:
    return CorpusAccessScope(
        mode=CorpusAccessMode.AUTHENTICATED,
        revision=revision,
        allowed_seasons=frozenset(),
        unrestricted=True,
    )


def make_guest_corpus_access_scope() -> CorpusAccessScope:
    return DEFAULT_GUEST_CORPUS_ACCESS_SCOPE


def make_episode_ref(
    *,
    series_id: UUID = DEFAULT_SERIES_ID,
    season_id: UUID = DEFAULT_SEASON_ID,
    episode_id: UUID = DEFAULT_EPISODE_ID,
    season_number: int = 1,
    episode_number: int = 1,
) -> EpisodeRef:
    return EpisodeRef(
        series_id=series_id,
        season_id=season_id,
        episode_id=episode_id,
        position=EpisodePosition(
            season_number=season_number,
            episode_number=episode_number,
        ),
    )
