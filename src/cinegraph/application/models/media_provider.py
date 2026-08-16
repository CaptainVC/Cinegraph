from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class MediaProviderHealth:
    provider_label: str
    connection_revision: str
    available: bool
    simulated: bool


@dataclass(frozen=True, slots=True)
class MediaProviderEpisode:
    episode_id: UUID
    provider_item_id: str
    title: str


@dataclass(frozen=True, slots=True)
class MediaProviderPlaylist:
    name: str
    episode_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class MediaPlaybackRequest:
    command_id: UUID
    episode_id: UUID


@dataclass(frozen=True, slots=True)
class MediaProviderProfileSnapshot:
    profile_id: UUID
    watched_episode_ids: frozenset[UUID]
    favorite_episode_ids: frozenset[UUID]
    playlists: tuple[MediaProviderPlaylist, ...]
    playback_requests: tuple[MediaPlaybackRequest, ...]
    state_revision: str
