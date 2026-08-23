from dataclasses import dataclass

from cinegraph.common.error_messages import CatalogueErrorMessages
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.catalogue.series import Series
from cinegraph.domain.models.watch_state import EpisodePosition, EpisodeRef

SUPPORTED_CATALOGUE_MANIFEST_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class CatalogueManifest:
    schema_version: int
    series: tuple[Series, ...]

    # Enforce stable global identities across the complete catalogue graph.
    def __post_init__(self) -> None:
        if self.schema_version != SUPPORTED_CATALOGUE_MANIFEST_SCHEMA_VERSION:
            raise InvalidModelError(
                CatalogueErrorMessages.CATALOGUE_SCHEMA_VERSION_MUST_BE_SUPPORTED
            )
        if not isinstance(self.series, tuple):
            raise InvalidModelError(
                CatalogueErrorMessages.CATALOGUE_SERIES_MUST_BE_IMMUTABLE
            )
        if not self.series:
            raise InvalidModelError(
                CatalogueErrorMessages.CATALOGUE_MUST_CONTAIN_SERIES
            )
        if len({item.series_id for item in self.series}) != len(self.series):
            raise InvalidModelError(
                CatalogueErrorMessages.CATALOGUE_SERIES_IDS_MUST_BE_UNIQUE
            )
        if len({item.series_name.casefold() for item in self.series}) != len(self.series):
            raise InvalidModelError(
                CatalogueErrorMessages.CATALOGUE_SERIES_NAMES_MUST_BE_UNIQUE
            )
        seasons = tuple(season for item in self.series for season in item.seasons)
        if len({season.season_id for season in seasons}) != len(seasons):
            raise InvalidModelError(
                CatalogueErrorMessages.CATALOGUE_SEASON_IDS_MUST_BE_GLOBALLY_UNIQUE
            )
        episodes = tuple(
            episode
            for season in seasons
            for episode in season.episodes
        )
        if len({episode.episode_id for episode in episodes}) != len(episodes):
            raise InvalidModelError(
                CatalogueErrorMessages.CATALOGUE_EPISODE_IDS_MUST_BE_GLOBALLY_UNIQUE
            )

    # Flatten the sorted catalogue into canonical references for policy and retrieval.
    def episode_refs(self) -> tuple[EpisodeRef, ...]:
        return tuple(
            EpisodeRef(
                series_id=series.series_id,
                season_id=season.season_id,
                episode_id=episode.episode_id,
                position=EpisodePosition(
                    season_number=season.season_number,
                    episode_number=episode.episode_number,
                ),
            )
            for series in self.series
            for season in series.seasons
            for episode in season.episodes
        )
