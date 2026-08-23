from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlparse
from uuid import UUID

from cinegraph.common.error_messages.source import SourceErrorMessages
from cinegraph.domain.enums.enum import RightsStatus
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef


def _url(value: str) -> None:
    if not isinstance(value, str):
        raise InvalidModelError(SourceErrorMessages.METADATA_URL_MUST_BE_HTTP)
    parsed = urlparse(value)
    if (
        not value
        or value.strip() != value
        or parsed.scheme not in {"http", "https"}
        or not parsed.netloc
    ):
        raise InvalidModelError(SourceErrorMessages.METADATA_URL_MUST_BE_HTTP)


def _text(value: str) -> None:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise InvalidModelError(SourceErrorMessages.METADATA_VALUE_MUST_BE_TRIMMED)


class CreditKind(StrEnum):
    REGULAR = "regular"
    GUEST = "guest"


@dataclass(frozen=True, slots=True)
class ArtworkAsset:
    source_url: str
    canonical_url: str
    medium_url: str | None
    original_url: str | None
    provider_asset_id: str | None
    width: int | None
    height: int | None
    attribution: str
    license_name: str
    license_url: str
    retrieved_at: datetime

    def __post_init__(self) -> None:
        for value in (self.source_url, self.canonical_url, self.license_url):
            _url(value)
        for optional_url in (self.medium_url, self.original_url):
            if optional_url is not None:
                _url(optional_url)
        for value in (self.attribution, self.license_name):
            _text(value)
        if self.provider_asset_id is not None:
            _text(self.provider_asset_id)
        if any(
            value is not None
            and (not isinstance(value, int) or isinstance(value, bool) or value < 1)
            for value in (self.width, self.height)
        ):
            raise InvalidModelError(
                SourceErrorMessages.METADATA_DIMENSIONS_MUST_BE_POSITIVE
            )
        if (
            not isinstance(self.retrieved_at, datetime)
            or self.retrieved_at.tzinfo is None
        ):
            raise InvalidModelError(
                SourceErrorMessages.METADATA_RETRIEVED_AT_MUST_BE_TIMEZONE_AWARE
            )

    @property
    def medium(self) -> str | None:
        return self.medium_url

    @property
    def original(self) -> str | None:
        return self.original_url


@dataclass(frozen=True, slots=True)
class CreditedPerson:
    provider_person_id: int
    name: str
    canonical_url: str
    character_name: str
    character_provider_id: int | None
    character_canonical_url: str | None
    credit_kind: CreditKind

    def __post_init__(self) -> None:
        if (
            not isinstance(self.provider_person_id, int)
            or isinstance(self.provider_person_id, bool)
            or self.provider_person_id < 1
            or (
                self.character_provider_id is not None
                and (
                    not isinstance(self.character_provider_id, int)
                    or isinstance(self.character_provider_id, bool)
                    or self.character_provider_id < 1
                )
            )
        ):
            raise InvalidModelError(
                SourceErrorMessages.METADATA_PROVIDER_ID_MUST_BE_POSITIVE
            )
        _text(self.name)
        _text(self.character_name)
        _url(self.canonical_url)
        if self.character_canonical_url is not None:
            _url(self.character_canonical_url)
        if not isinstance(self.credit_kind, CreditKind):
            raise InvalidModelError(SourceErrorMessages.METADATA_VALUE_MUST_BE_TRIMMED)

    @property
    def is_guest(self) -> bool:
        return self.credit_kind is CreditKind.GUEST

    @property
    def person_external_id(self) -> int:
        return self.provider_person_id

    @property
    def character_external_id(self) -> int | None:
        return self.character_provider_id


@dataclass(frozen=True, slots=True)
class EpisodeCastMetadata:
    episode: EpisodeRef
    provider_episode_id: int
    title: str
    canonical_url: str
    guest_cast: tuple[CreditedPerson, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.episode, EpisodeRef):
            raise InvalidModelError(SourceErrorMessages.METADATA_VALUE_MUST_BE_TRIMMED)
        if (
            not isinstance(self.provider_episode_id, int)
            or isinstance(self.provider_episode_id, bool)
            or self.provider_episode_id < 1
        ):
            raise InvalidModelError(
                SourceErrorMessages.METADATA_PROVIDER_ID_MUST_BE_POSITIVE
            )
        _text(self.title)
        _url(self.canonical_url)
        if not isinstance(self.guest_cast, tuple) or any(
            not isinstance(credit, CreditedPerson)
            or credit.credit_kind is not CreditKind.GUEST
            for credit in self.guest_cast
        ):
            raise InvalidModelError(SourceErrorMessages.METADATA_VALUE_MUST_BE_TRIMMED)

    @property
    def season_number(self) -> int:
        return self.episode.position.season_number

    @property
    def episode_number(self) -> int:
        return self.episode.position.episode_number


@dataclass(frozen=True, slots=True)
class SeriesMetadataSnapshot:
    series_id: UUID
    source_version_id: UUID
    provider_name: str
    provider_show_id: int
    title: str
    canonical_url: str
    poster: ArtworkAsset | None
    regular_cast: tuple[CreditedPerson, ...]
    episodes: tuple[EpisodeCastMetadata, ...]
    rights_status: RightsStatus
    attribution: str
    license_name: str
    license_url: str

    def __post_init__(self) -> None:
        if not isinstance(self.series_id, UUID) or not isinstance(
            self.source_version_id, UUID
        ):
            raise InvalidModelError(SourceErrorMessages.METADATA_VALUE_MUST_BE_TRIMMED)
        if (
            not isinstance(self.provider_show_id, int)
            or isinstance(self.provider_show_id, bool)
            or self.provider_show_id < 1
        ):
            raise InvalidModelError(
                SourceErrorMessages.METADATA_PROVIDER_ID_MUST_BE_POSITIVE
            )
        _text(self.provider_name)
        _text(self.title)
        _url(self.canonical_url)
        _text(self.attribution)
        _text(self.license_name)
        _url(self.license_url)
        if (
            not isinstance(self.regular_cast, tuple)
            or not isinstance(self.episodes, tuple)
            or not self.episodes
            or not isinstance(self.rights_status, RightsStatus)
        ):
            raise InvalidModelError(SourceErrorMessages.METADATA_VALUE_MUST_BE_TRIMMED)
        if any(
            not isinstance(credit, CreditedPerson)
            or credit.credit_kind is not CreditKind.REGULAR
            for credit in self.regular_cast
        ):
            raise InvalidModelError(SourceErrorMessages.METADATA_VALUE_MUST_BE_TRIMMED)
        if any(
            not isinstance(episode, EpisodeCastMetadata)
            or episode.episode.series_id != self.series_id
            for episode in self.episodes
        ):
            raise InvalidModelError(SourceErrorMessages.METADATA_VALUE_MUST_BE_TRIMMED)
        positions = [e.episode.position for e in self.episodes]
        if positions != sorted(positions) or len(set(positions)) != len(positions):
            raise InvalidModelError(SourceErrorMessages.METADATA_VALUE_MUST_BE_TRIMMED)
