from dataclasses import dataclass, field
from urllib.parse import urlparse
from uuid import UUID

from cinegraph.common.error_messages import MediaProviderErrorMessages

JELLYFIN_PUBLIC_SYSTEM_INFO_PATH = "/System/Info/Public"
JELLYFIN_ITEMS_PATH = "/Items"
JELLYFIN_USER_DATA_PATH = "/UserItems/{item_id}/UserData"
JELLYFIN_PLAYED_ITEMS_PATH = "/UserPlayedItems/{item_id}"
JELLYFIN_FAVORITE_ITEMS_PATH = "/UserFavoriteItems/{item_id}"
JELLYFIN_PLAYLISTS_PATH = "/Playlists"
JELLYFIN_PLAYLIST_ITEMS_PATH = "/Playlists/{playlist_id}/Items"
JELLYFIN_SESSION_PLAY_PATH = "/Sessions/{session_id}/Playing"
JELLYFIN_SESSIONS_PATH = "/Sessions"
JELLYFIN_EPISODE_ITEM_TYPE = "Episode"
JELLYFIN_PLAYLIST_ITEM_TYPE = "Playlist"
JELLYFIN_VIDEO_MEDIA_TYPE = "Video"
JELLYFIN_PLAY_NOW_COMMAND = "PlayNow"
JELLYFIN_UNAVAILABLE_REVISION = "jellyfin-unavailable"


@dataclass(frozen=True, slots=True)
class JellyfinEpisodeMapping:
    episode_id: UUID
    provider_item_id: str
    title: str

    def __post_init__(self) -> None:
        if not isinstance(self.episode_id, UUID) or any(
            not value or value.strip() != value
            for value in (self.provider_item_id, self.title)
        ):
            raise ValueError(
                MediaProviderErrorMessages.CONNECTION_CONFIGURATION_INVALID
            )


@dataclass(frozen=True, slots=True)
class JellyfinConnectionSettings:
    connection_id: UUID
    profile_id: UUID
    user_id: str
    base_url: str
    access_token: str = field(repr=False)
    device_id: str = "cinegraph"
    playback_session_id: str | None = None
    episode_mappings: tuple[JellyfinEpisodeMapping, ...] = ()

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        identifiers_valid = isinstance(self.connection_id, UUID) and isinstance(
            self.profile_id, UUID
        )
        strings = (self.user_id, self.base_url, self.access_token, self.device_id)
        mappings_unique = len(
            {mapping.episode_id for mapping in self.episode_mappings}
        ) == len(self.episode_mappings) and len(
            {mapping.provider_item_id for mapping in self.episode_mappings}
        ) == len(self.episode_mappings)
        if (
            not identifiers_valid
            or any(not value or value.strip() != value for value in strings)
            or parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.query
            or parsed.fragment
            or self.base_url.endswith("/")
            or not self.episode_mappings
            or not mappings_unique
        ):
            raise ValueError(
                MediaProviderErrorMessages.CONNECTION_CONFIGURATION_INVALID
            )


@dataclass(frozen=True, slots=True)
class JellyfinProviderConfiguration:
    provider_label: str
    client_name: str
    client_version: str
    device_name: str
    timeout_seconds: float
    maximum_attempts: int
    retry_backoff_seconds: float
    retryable_status_codes: frozenset[int]
    circuit_failure_threshold: int
    circuit_recovery_seconds: float

    def __post_init__(self) -> None:
        if (
            any(
                not value or value.strip() != value
                for value in (
                    self.provider_label,
                    self.client_name,
                    self.client_version,
                    self.device_name,
                )
            )
            or self.timeout_seconds <= 0
            or self.maximum_attempts < 1
            or self.retry_backoff_seconds < 0
            or not self.retryable_status_codes
            or self.circuit_failure_threshold < 1
            or self.circuit_recovery_seconds <= 0
        ):
            raise ValueError(
                MediaProviderErrorMessages.CONNECTION_CONFIGURATION_INVALID
            )


DEFAULT_JELLYFIN_PROVIDER_CONFIGURATION = JellyfinProviderConfiguration(
    provider_label="Jellyfin",
    client_name="CineGraph",
    client_version="0.1.0",
    device_name="CineGraph server",
    timeout_seconds=5.0,
    maximum_attempts=3,
    retry_backoff_seconds=0.2,
    retryable_status_codes=frozenset({408, 429, 500, 502, 503, 504}),
    circuit_failure_threshold=3,
    circuit_recovery_seconds=30.0,
)
