from __future__ import annotations

from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx

from cinegraph.adapters.source.tvmaze_constants import (
    TVMAZE_ALLOWED_CONTENT_HOSTS,
    TVMAZE_ATTRIBUTION,
    TVMAZE_BASE_URL,
    TVMAZE_LICENSE_NAME,
    TVMAZE_LICENSE_URL,
    TVMAZE_PROVIDER_NAME,
)
from cinegraph.common.error_messages.source import SourceErrorMessages
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.exceptions.tvmaze_errors import (
    TVMazeEpisodeReconciliationError,
    TVMazeProviderError,
    TVMazeShowMismatchError,
)
from cinegraph.domain.models.series_metadata import (
    ArtworkAsset,
    CreditedPerson,
    CreditKind,
    EpisodeCastMetadata,
)
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef
from cinegraph.ports.date_time.clock import Clock
from cinegraph.ports.dto.fetched_series_metadata import FetchedSeriesMetadata
from cinegraph.ports.series_metadata.series_metadata_provider import (
    SeriesMetadataProvider,
)


class TVMazeSeriesMetadataProvider(SeriesMetadataProvider):
    def __init__(
        self,
        client: httpx.Client,
        clock: Clock,
        *,
        base_url: str = TVMAZE_BASE_URL,
    ) -> None:
        self._client = client
        self._clock = clock
        self._base_url = base_url.rstrip("/")

    def _get(self, path: str) -> Any:
        try:
            response = self._client.get(f"{self._base_url}{path}")
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise TVMazeProviderError(
                SourceErrorMessages.TVMAZE_HTTP_ERROR.format(detail=str(error))
            ) from error

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise TVMazeProviderError(SourceErrorMessages.TVMAZE_RESPONSE_MALFORMED)
        return value

    @staticmethod
    def _positive_id(value: Any) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise TVMazeProviderError(SourceErrorMessages.TVMAZE_RESPONSE_MALFORMED)
        return value

    @staticmethod
    def _season_number(value: Any) -> int | None:
        # TVmaze may include unnumbered specials. They cannot match Cinegraph's
        # positive catalogue positions, so ignore them without rejecting the
        # otherwise valid season payload.
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise TVMazeProviderError(SourceErrorMessages.TVMAZE_RESPONSE_MALFORMED)
        return value

    @staticmethod
    def _trimmed(value: Any) -> str:
        if not isinstance(value, str) or not value or value.strip() != value:
            raise TVMazeProviderError(SourceErrorMessages.TVMAZE_RESPONSE_MALFORMED)
        return value

    @classmethod
    def _require_url(cls, value: Any) -> str:
        value = cls._trimmed(value)
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in TVMAZE_ALLOWED_CONTENT_HOSTS
        ):
            raise TVMazeProviderError(SourceErrorMessages.TVMAZE_RESPONSE_MALFORMED)
        return value

    @classmethod
    def _person(cls, raw: Any, kind: CreditKind) -> CreditedPerson:
        try:
            item = cls._mapping(raw)
            person = cls._mapping(item["person"])
            character = cls._mapping(item["character"])
            character_id = character.get("id")
            return CreditedPerson(
                provider_person_id=cls._positive_id(person["id"]),
                name=cls._trimmed(person["name"]),
                canonical_url=cls._require_url(person["url"]),
                character_name=cls._trimmed(character["name"]),
                character_provider_id=(
                    cls._positive_id(character_id) if character_id is not None else None
                ),
                character_canonical_url=(
                    cls._require_url(character["url"])
                    if character.get("url") is not None
                    else None
                ),
                credit_kind=kind,
            )
        except (KeyError, TypeError, ValueError, InvalidModelError) as error:
            if isinstance(error, TVMazeProviderError):
                raise
            raise TVMazeProviderError(
                SourceErrorMessages.TVMAZE_RESPONSE_MALFORMED
            ) from error

    def fetch(
        self,
        *,
        provider_show_id: int,
        expected_title: str,
        series_id: UUID,
        episodes: tuple[EpisodeRef, ...],
    ) -> FetchedSeriesMetadata:
        if (
            not isinstance(provider_show_id, int)
            or isinstance(provider_show_id, bool)
            or provider_show_id < 1
        ):
            raise ValueError(SourceErrorMessages.TVMAZE_SHOW_ID_MUST_BE_POSITIVE)
        if not isinstance(series_id, UUID):
            raise ValueError(SourceErrorMessages.TVMAZE_EPISODE_SERIES_MISMATCH)
        if not isinstance(episodes, tuple):
            raise ValueError(
                SourceErrorMessages.SERIES_METADATA_EPISODE_SCOPE_MUST_BE_IMMUTABLE
            )
        if (
            not isinstance(expected_title, str)
            or not expected_title
            or expected_title.strip() != expected_title
        ):
            raise ValueError(SourceErrorMessages.METADATA_VALUE_MUST_BE_TRIMMED)
        if not episodes:
            raise ValueError(SourceErrorMessages.TVMAZE_EPISODE_SCOPE_MUST_BE_NON_EMPTY)
        if any(episode.series_id != series_id for episode in episodes):
            raise ValueError(SourceErrorMessages.TVMAZE_EPISODE_SERIES_MISMATCH)

        requested: dict[tuple[int, int], EpisodeRef] = {}
        for episode in episodes:
            key = (episode.position.season_number, episode.position.episode_number)
            if key in requested:
                raise ValueError(SourceErrorMessages.TVMAZE_DUPLICATE_REQUESTED_EPISODE)
            requested[key] = episode

        show = self._mapping(self._get(f"/shows/{provider_show_id}"))
        show_id = self._positive_id(show.get("id"))
        if show_id != provider_show_id:
            raise TVMazeProviderError(SourceErrorMessages.TVMAZE_SHOW_ID_MISMATCH)
        title = self._trimmed(show.get("name"))
        canonical_url = self._require_url(show.get("url"))
        if title.casefold() != expected_title.casefold():
            raise TVMazeShowMismatchError(
                SourceErrorMessages.METADATA_SHOW_TITLE_MISMATCH
            )

        poster = self._poster(show, canonical_url)
        cast_payload = self._get(f"/shows/{provider_show_id}/cast")
        if not isinstance(cast_payload, list):
            raise TVMazeProviderError(SourceErrorMessages.TVMAZE_RESPONSE_MALFORMED)
        regular_cast = tuple(
            self._person(item, CreditKind.REGULAR) for item in cast_payload
        )

        seasons_payload = self._get(f"/shows/{provider_show_id}/seasons")
        if not isinstance(seasons_payload, list):
            raise TVMazeProviderError(SourceErrorMessages.TVMAZE_RESPONSE_MALFORMED)
        requested_seasons = {season for season, _ in requested}
        seasons: dict[int, int] = {}
        for raw in seasons_payload:
            season = self._mapping(raw)
            season_number = self._season_number(season.get("number"))
            if season_number not in requested_seasons:
                continue
            season_id = self._positive_id(season.get("id"))
            if season_number in seasons:
                raise TVMazeEpisodeReconciliationError(
                    SourceErrorMessages.TVMAZE_DUPLICATE_SEASON
                )
            seasons[season_number] = season_id
        if requested_seasons - seasons.keys():
            raise TVMazeEpisodeReconciliationError(
                SourceErrorMessages.TVMAZE_SEASON_NOT_FOUND
            )

        mapped: dict[tuple[int, int], EpisodeCastMetadata] = {}
        for season_number in sorted(requested_seasons):
            season_episodes = self._get(
                f"/seasons/{seasons[season_number]}/episodes?embed=guestcast"
            )
            if not isinstance(season_episodes, list):
                raise TVMazeProviderError(SourceErrorMessages.TVMAZE_RESPONSE_MALFORMED)
            for raw in season_episodes:
                episode = self._mapping(raw)
                provider_season_number = self._season_number(episode.get("season"))
                provider_episode_number = episode.get("number")
                if provider_season_number is None or provider_episode_number is None:
                    continue
                position = (
                    provider_season_number,
                    self._positive_id(provider_episode_number),
                )
                if position not in requested:
                    continue
                if position in mapped:
                    raise TVMazeEpisodeReconciliationError(
                        SourceErrorMessages.TVMAZE_DUPLICATE_EPISODE
                    )
                embedded = episode.get("_embedded")
                if not isinstance(embedded, dict) or not isinstance(
                    embedded.get("guestcast"), list
                ):
                    raise TVMazeProviderError(
                        SourceErrorMessages.TVMAZE_RESPONSE_MALFORMED
                    )
                guest_cast = tuple(
                    self._person(item, CreditKind.GUEST)
                    for item in embedded["guestcast"]
                )
                mapped[position] = EpisodeCastMetadata(
                    episode=requested[position],
                    provider_episode_id=self._positive_id(episode.get("id")),
                    title=self._trimmed(episode.get("name")),
                    canonical_url=self._require_url(episode.get("url")),
                    guest_cast=guest_cast,
                )
        if requested.keys() - mapped.keys():
            raise TVMazeEpisodeReconciliationError(
                SourceErrorMessages.TVMAZE_EPISODE_NOT_FOUND
            )
        return FetchedSeriesMetadata(
            provider_name=TVMAZE_PROVIDER_NAME,
            provider_show_id=provider_show_id,
            title=title,
            canonical_url=canonical_url,
            poster=poster,
            regular_cast=regular_cast,
            episodes=tuple(mapped[key] for key in sorted(mapped)),
            retrieved_at=self._clock.now_utc(),
            attribution=TVMAZE_ATTRIBUTION,
            license_name=TVMAZE_LICENSE_NAME,
            license_url=TVMAZE_LICENSE_URL,
        )

    def _poster(self, show: dict[str, Any], canonical_url: str) -> ArtworkAsset | None:
        image = show.get("image")
        if image is None:
            return None
        image = self._mapping(image)
        medium = image.get("medium")
        original = image.get("original")
        image_url = original or medium
        if image_url is None:
            raise TVMazeProviderError(SourceErrorMessages.TVMAZE_RESPONSE_MALFORMED)
        image_url = self._require_url(image_url)
        asset_path = urlparse(image_url).path
        try:
            return ArtworkAsset(
                source_url=image_url,
                canonical_url=canonical_url,
                medium_url=self._require_url(medium) if medium is not None else None,
                original_url=self._require_url(original)
                if original is not None
                else None,
                provider_asset_id=asset_path or None,
                width=None,
                height=None,
                attribution=TVMAZE_ATTRIBUTION,
                license_name=TVMAZE_LICENSE_NAME,
                license_url=TVMAZE_LICENSE_URL,
                retrieved_at=self._clock.now_utc(),
            )
        except (InvalidModelError, ValueError) as error:
            raise TVMazeProviderError(
                SourceErrorMessages.TVMAZE_RESPONSE_MALFORMED
            ) from error
