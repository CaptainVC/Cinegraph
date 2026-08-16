from dataclasses import dataclass
from uuid import UUID

from cinegraph.common.error_messages import AccessErrorMessages
from cinegraph.domain.enums.enum import CorpusAccessMode
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef


@dataclass(frozen=True, slots=True, order=True)
class CorpusSeasonAccess:
    series_id: UUID
    season_number: int

    def __post_init__(self) -> None:
        if not isinstance(self.series_id, UUID):
            raise InvalidModelError(
                AccessErrorMessages.CORPUS_SEASON_SERIES_ID_MUST_BE_UUID
            )
        if (
            isinstance(self.season_number, bool)
            or not isinstance(self.season_number, int)
            or self.season_number < 1
        ):
            raise InvalidModelError(
                AccessErrorMessages.CORPUS_SEASON_NUMBER_MUST_BE_POSITIVE
            )


@dataclass(frozen=True, slots=True)
class CorpusAccessScope:
    mode: CorpusAccessMode
    revision: str
    allowed_seasons: frozenset[CorpusSeasonAccess]
    unrestricted: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.mode, CorpusAccessMode):
            raise InvalidModelError(
                AccessErrorMessages.CORPUS_ACCESS_MODE_MUST_BE_VALID
            )
        if not isinstance(self.unrestricted, bool):
            raise InvalidModelError(
                AccessErrorMessages.CORPUS_ACCESS_UNRESTRICTED_MUST_BE_BOOLEAN
            )
        if not self.revision or self.revision.strip() != self.revision:
            raise InvalidModelError(
                AccessErrorMessages.CORPUS_SCOPE_REVISION_MUST_BE_NONEMPTY
            )
        if not isinstance(self.allowed_seasons, frozenset):
            raise InvalidModelError(
                AccessErrorMessages.CORPUS_SCOPE_ALLOWED_SEASONS_MUST_BE_IMMUTABLE
            )
        if not all(
            isinstance(item, CorpusSeasonAccess) for item in self.allowed_seasons
        ):
            raise InvalidModelError(
                AccessErrorMessages.CORPUS_SCOPE_ALLOWED_SEASONS_MUST_BE_VALID
            )
        if self.mode is CorpusAccessMode.GUEST and self.unrestricted:
            raise InvalidModelError(
                AccessErrorMessages.GUEST_CORPUS_SCOPE_CANNOT_BE_UNRESTRICTED
            )
        if self.mode is CorpusAccessMode.GUEST and not self.allowed_seasons:
            raise InvalidModelError(
                AccessErrorMessages.GUEST_CORPUS_SCOPE_REQUIRES_ALLOWED_SEASONS
            )

    def allows_episode(self, episode: EpisodeRef) -> bool:
        return (
            self.unrestricted
            or CorpusSeasonAccess(
                series_id=episode.series_id,
                season_number=episode.position.season_number,
            )
            in self.allowed_seasons
        )

    def allows_all(self, episodes: tuple[EpisodeRef, ...]) -> bool:
        return bool(episodes) and all(
            self.allows_episode(episode) for episode in episodes
        )
