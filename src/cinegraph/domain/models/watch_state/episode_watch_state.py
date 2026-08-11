from dataclasses import dataclass
from uuid import UUID

from cinegraph.common.error_messages import WatchErrorMessages
from cinegraph.domain.exceptions.errors import InvalidModelError

@dataclass(frozen=True, slots=True, order=True)
class EpisodePosition:
    season_number: int
    episode_number: int

    # Validates the initialized value after construction.
    def __post_init__(self) -> None:
        if self.season_number < 1:
            raise InvalidModelError(
                WatchErrorMessages.EPISODE_POSITION_SEASON_MUST_BE_POSITIVE
            )
        if self.episode_number < 1:
            raise InvalidModelError(
                WatchErrorMessages.EPISODE_POSITION_EPISODE_MUST_BE_POSITIVE
            )

@dataclass(frozen=True, slots=True)
class EpisodeRef:
    series_id: UUID
    season_id: UUID
    episode_id: UUID
    position: EpisodePosition

@dataclass(frozen=True, slots=True)
class EpisodeWatchProgress:
    episode: EpisodeRef
    is_completed: bool = False
    safe_until_ms: int | None = None

    # Validates the initialized value after construction.
    def __post_init__(self) -> None:
        if self.safe_until_ms is not None and self.safe_until_ms < 0:
            raise InvalidModelError(
                WatchErrorMessages.SAFE_UNTIL_MS_MUST_BE_NON_NEGATIVE
            )
        if self.is_completed and self.safe_until_ms is not None:
            raise InvalidModelError(
                WatchErrorMessages.COMPLETED_EPISODE_CANNOT_HAVE_SAFE_UNTIL_MS
            )
        if not self.is_completed and self.safe_until_ms is None:
            raise InvalidModelError(
                WatchErrorMessages.PARTIAL_EPISODE_REQUIRES_SAFE_UNTIL_MS
            )
