from dataclasses import dataclass
from uuid import UUID

from cinegraph.common.error_messages import CatalogueErrorMessages
from cinegraph.domain.exceptions.errors import InvalidModelError

@dataclass(frozen=True, slots=True)
class Episode:
    series_id: UUID
    season_id: UUID
    episode_id: UUID
    episode_number: int
    synopsis: str | None = None
    runtime_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.episode_number < 1:
            raise InvalidModelError(
                CatalogueErrorMessages.EPISODE_NUMBER_MUST_BE_POSITIVE
            )
        if self.runtime_seconds is not None and self.runtime_seconds < 1:
            raise InvalidModelError(
                CatalogueErrorMessages.EPISODE_RUNTIME_MUST_BE_POSITIVE
            )