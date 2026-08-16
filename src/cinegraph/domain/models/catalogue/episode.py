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
    episode_title: str | None = None
    synopsis: str | None = None
    runtime_seconds: int | None = None

    # Require a positive episode number and non-empty trimmed episode title.
    def __post_init__(self) -> None:
        if self.episode_number < 1:
            raise InvalidModelError(
                CatalogueErrorMessages.EPISODE_NUMBER_MUST_BE_POSITIVE
            )
        if self.episode_title is not None and (
            not self.episode_title or self.episode_title.strip() != self.episode_title
        ):
            raise InvalidModelError(
                CatalogueErrorMessages.EPISODE_TITLE_MUST_BE_TRIMMED
            )
        if self.runtime_seconds is not None and self.runtime_seconds < 1:
            raise InvalidModelError(
                CatalogueErrorMessages.EPISODE_RUNTIME_MUST_BE_POSITIVE
            )
