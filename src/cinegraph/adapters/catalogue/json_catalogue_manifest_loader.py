import hashlib
import json
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from cinegraph.common.error_messages import CatalogueErrorMessages
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.catalogue.catalogue_manifest import CatalogueManifest
from cinegraph.domain.models.catalogue.episode import Episode
from cinegraph.domain.models.catalogue.season import Season
from cinegraph.domain.models.catalogue.series import Series
from cinegraph.ports.catalogue import LoadedCatalogueManifest


class _StrictManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _EpisodeModel(_StrictManifestModel):
    episode_id: UUID
    episode_number: StrictInt = Field(ge=1)
    episode_title: StrictStr
    reviewed_subtitle_filename: StrictStr | None = None
    synopsis: StrictStr | None = None
    runtime_seconds: StrictInt | None = Field(default=None, ge=1)


class _SeasonModel(_StrictManifestModel):
    season_id: UUID
    season_number: StrictInt = Field(ge=1)
    episodes: tuple[_EpisodeModel, ...] = Field(min_length=1)


class _SeriesModel(_StrictManifestModel):
    series_id: UUID
    series_name: StrictStr
    seasons: tuple[_SeasonModel, ...] = Field(min_length=1)


class _CatalogueModel(_StrictManifestModel):
    schema_version: StrictInt
    series: tuple[_SeriesModel, ...] = Field(min_length=1)


class JsonCatalogueManifestLoader:
    # Parse a strict versioned JSON manifest into a sorted immutable domain graph.
    def load(self, path: Path) -> LoadedCatalogueManifest:
        if not isinstance(path, Path) or not path.is_file():
            raise InvalidModelError(
                CatalogueErrorMessages.CATALOGUE_MANIFEST_PATH_MUST_BE_FILE
            )
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as error:
            raise InvalidModelError(
                CatalogueErrorMessages.CATALOGUE_MANIFEST_PATH_MUST_BE_FILE
            ) from error
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as error:
            raise InvalidModelError(
                CatalogueErrorMessages.CATALOGUE_MANIFEST_JSON_MUST_BE_VALID
            ) from error
        try:
            source = _CatalogueModel.model_validate(decoded)
            manifest = self._map_manifest(source)
        except (ValidationError, InvalidModelError) as error:
            raise InvalidModelError(
                CatalogueErrorMessages.CATALOGUE_MANIFEST_STRUCTURE_MUST_BE_VALID
            ) from error

        canonical_json = json.dumps(
            self._canonical_data(manifest),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return LoadedCatalogueManifest(
            manifest=manifest,
            content_sha256=hashlib.sha256(canonical_json.encode("utf-8")).hexdigest(),
        )

    @staticmethod
    def _map_manifest(source: _CatalogueModel) -> CatalogueManifest:
        series_items = []
        for source_series in source.series:
            seasons = []
            for source_season in source_series.seasons:
                episodes = tuple(
                    sorted(
                        (
                            Episode(
                                series_id=source_series.series_id,
                                season_id=source_season.season_id,
                                episode_id=item.episode_id,
                                episode_number=item.episode_number,
                                episode_title=item.episode_title,
                                reviewed_subtitle_filename=(
                                    item.reviewed_subtitle_filename
                                ),
                                synopsis=item.synopsis,
                                runtime_seconds=item.runtime_seconds,
                            )
                            for item in source_season.episodes
                        ),
                        key=lambda item: item.episode_number,
                    )
                )
                seasons.append(
                    Season(
                        series_id=source_series.series_id,
                        season_id=source_season.season_id,
                        season_number=source_season.season_number,
                        episodes=episodes,
                    )
                )
            series_items.append(
                Series(
                    series_id=source_series.series_id,
                    series_name=source_series.series_name,
                    seasons=tuple(sorted(seasons, key=lambda item: item.season_number)),
                )
            )
        return CatalogueManifest(
            schema_version=source.schema_version,
            series=tuple(
                sorted(
                    series_items,
                    key=lambda item: (item.series_name.casefold(), str(item.series_id)),
                )
            ),
        )

    @staticmethod
    def _canonical_data(manifest: CatalogueManifest) -> dict[str, object]:
        return {
            "schema_version": manifest.schema_version,
            "series": [
                {
                    "series_id": str(series.series_id),
                    "series_name": series.series_name,
                    "seasons": [
                        {
                            "season_id": str(season.season_id),
                            "season_number": season.season_number,
                            "episodes": [
                                {
                                    "episode_id": str(episode.episode_id),
                                    "episode_number": episode.episode_number,
                                    "episode_title": episode.episode_title,
                                    "reviewed_subtitle_filename": (
                                        episode.reviewed_subtitle_filename
                                    ),
                                    "runtime_seconds": episode.runtime_seconds,
                                    "synopsis": episode.synopsis,
                                }
                                for episode in season.episodes
                            ],
                        }
                        for season in series.seasons
                    ],
                }
                for series in manifest.series
            ],
        }
