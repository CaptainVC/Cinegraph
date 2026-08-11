from dataclasses import dataclass

from uuid import UUID

from cinegraph.common.error_messages import CatalogueErrorMessages
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.catalogue.episode import Episode


@dataclass(frozen=True, slots=True)
class Season:
    series_id: UUID
    season_id: UUID
    season_number: int
    episodes: tuple[Episode, ...]

    # Validates the initialized value after construction.
    def __post_init__(self) -> None:
        if self.season_number < 1:
            raise InvalidModelError(
                CatalogueErrorMessages.SEASON_NUMBER_MUST_BE_POSITIVE
            )
        if not self.episodes:
            raise InvalidModelError(
                CatalogueErrorMessages.SEASON_MUST_CONTAIN_EPISODES
            )
        if not isinstance(self.episodes, tuple):
            raise InvalidModelError(
                CatalogueErrorMessages.SEASON_EPISODES_MUST_BE_IMMUTABLE
            )
        if any(
            episode.series_id != self.series_id or episode.season_id != self.season_id
            for episode in self.episodes
        ):
            raise InvalidModelError(
                CatalogueErrorMessages.SEASON_EPISODES_MUST_MATCH_CONTAINER
            )
        if len({episode.episode_id for episode in self.episodes}) != len(self.episodes):
            raise InvalidModelError(
                CatalogueErrorMessages.SEASON_CANNOT_HAVE_DUPLICATE_EPISODE_IDS
            )
        if len({episode.episode_number for episode in self.episodes}) != len(self.episodes):
            raise InvalidModelError(
                CatalogueErrorMessages.SEASON_CANNOT_HAVE_DUPLICATE_EPISODE_NUMBERS
            )
