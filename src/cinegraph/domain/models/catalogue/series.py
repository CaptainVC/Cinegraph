from dataclasses import dataclass
from uuid import UUID

from cinegraph.common.error_messages import CatalogueErrorMessages
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.catalogue.season import Season

@dataclass(frozen=True, slots=True)
class Series:
    series_id: UUID
    series_name: str
    seasons: tuple[Season, ...]

    # Validates the initialized value after construction.
    def __post_init__(self) -> None:
        if not self.series_name or self.series_name.strip() != self.series_name:
            raise InvalidModelError(
                CatalogueErrorMessages.SERIES_NAME_MUST_BE_TRIMMED
            )
        if not self.seasons:
            raise InvalidModelError(
                CatalogueErrorMessages.SERIES_MUST_CONTAIN_SEASONS
            )
        if not isinstance(self.seasons, tuple):
            raise InvalidModelError(
                CatalogueErrorMessages.SERIES_SEASONS_MUST_BE_IMMUTABLE
            )
        if any(season.series_id != self.series_id for season in self.seasons):
            raise InvalidModelError(
                CatalogueErrorMessages.SERIES_SEASONS_MUST_MATCH_CONTAINER
            )
        if len({season.season_id for season in self.seasons}) != len(self.seasons):
            raise InvalidModelError(
                CatalogueErrorMessages.SERIES_CANNOT_HAVE_DUPLICATE_SEASON_IDS
            )
        if len({season.season_number for season in self.seasons}) != len(self.seasons):
            raise InvalidModelError(
                CatalogueErrorMessages.SERIES_CANNOT_HAVE_DUPLICATE_SEASON_NUMBERS
            )
