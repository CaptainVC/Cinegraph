import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from uuid import UUID

from cinegraph.application.models.media_action import MediaActionResult
from cinegraph.application.models.media_provider import (
    MediaPlaybackRequest,
    MediaProviderEpisode,
    MediaProviderHealth,
    MediaProviderPlaylist,
    MediaProviderProfileSnapshot,
)
from cinegraph.common.error_messages import MediaProviderErrorMessages
from cinegraph.config import (
    DEFAULT_MOCK_MEDIA_PROVIDER_CONFIGURATION,
    MockMediaProviderConfiguration,
)
from cinegraph.domain.enums.enum import MediaCommandKind
from cinegraph.domain.models.media_action import MediaCommand


@dataclass(frozen=True, slots=True)
class MockMediaProviderProfileSeed:
    profile_id: UUID
    watched_episode_ids: frozenset[UUID] = frozenset()
    favorite_episode_ids: frozenset[UUID] = frozenset()


@dataclass(frozen=True, slots=True)
class MockMediaProviderSeed:
    connection_id: UUID
    episodes: tuple[MediaProviderEpisode, ...]
    profiles: tuple[MockMediaProviderProfileSeed, ...]


@dataclass(slots=True)
class _MutableProfileState:
    watched_episode_ids: set[UUID]
    favorite_episode_ids: set[UUID]
    playlists: dict[str, tuple[UUID, ...]]
    playback_requests: list[MediaPlaybackRequest]


class MockMediaProvider:
    """Deterministic demo/test adapter that never controls a real media server."""

    def __init__(
        self,
        seed: MockMediaProviderSeed,
        configuration: MockMediaProviderConfiguration = (
            DEFAULT_MOCK_MEDIA_PROVIDER_CONFIGURATION
        ),
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._validate_seed(seed)
        self._connection_id = seed.connection_id
        self._episodes = {episode.episode_id: episode for episode in seed.episodes}
        self._profiles = {
            profile.profile_id: _MutableProfileState(
                watched_episode_ids=set(profile.watched_episode_ids),
                favorite_episode_ids=set(profile.favorite_episode_ids),
                playlists={},
                playback_requests=[],
            )
            for profile in seed.profiles
        }
        self._configuration = configuration
        self._connection_revision = configuration.connection_revision
        self._available = not configuration.unavailable
        self._state_version = 1
        self._executions: dict[str, tuple[str, MediaActionResult]] = {}
        self._sleeper = sleeper
        self._lock = RLock()

    def health(self, provider_connection_id: UUID) -> MediaProviderHealth:
        self._require_connection(provider_connection_id)
        self._delay()
        return MediaProviderHealth(
            provider_label=self._configuration.provider_label,
            connection_revision=self._connection_revision,
            available=self._available,
            simulated=True,
        )

    def connection_revision(self, provider_connection_id: UUID) -> str:
        self._require_ready(provider_connection_id)
        return self._connection_revision

    def list_library(
        self,
        provider_connection_id: UUID,
        profile_id: UUID,
    ) -> tuple[MediaProviderEpisode, ...]:
        with self._lock:
            self._require_ready(provider_connection_id)
            self._require_profile(profile_id)
            self._delay()
            return tuple(sorted(self._episodes.values(), key=lambda item: item.episode_id))

    def profile_snapshot(
        self,
        provider_connection_id: UUID,
        profile_id: UUID,
    ) -> MediaProviderProfileSnapshot:
        with self._lock:
            self._require_ready(provider_connection_id)
            state = self._require_profile(profile_id)
            self._delay()
            return MediaProviderProfileSnapshot(
                profile_id=profile_id,
                watched_episode_ids=frozenset(state.watched_episode_ids),
                favorite_episode_ids=frozenset(state.favorite_episode_ids),
                playlists=tuple(
                    MediaProviderPlaylist(name, episode_ids)
                    for name, episode_ids in sorted(state.playlists.items())
                ),
                playback_requests=tuple(state.playback_requests),
                state_revision=self._state_revision,
            )

    def execute(self, command: MediaCommand) -> MediaActionResult:
        with self._lock:
            self._require_ready(command.provider_connection_id)
            state = self._require_profile(command.profile_id)
            self._require_episodes(command.episode_ids)
            self._delay()
            if command.kind in self._configuration.failing_commands:
                raise RuntimeError(MediaProviderErrorMessages.COMMAND_FAILED)
            prior = self._executions.get(command.idempotency_key)
            if prior is not None:
                command_sha256, result = prior
                if command_sha256 != command.parameter_sha256:
                    raise ValueError(MediaProviderErrorMessages.IDEMPOTENCY_KEY_REUSED)
                return MediaActionResult(
                    command_id=result.command_id,
                    external_reference=result.external_reference,
                    provider_state_revision=result.provider_state_revision,
                    idempotent_replay=True,
                )
            if not self._configuration.stale_writes:
                self._apply(command, state)
            self._state_version += 1
            result = MediaActionResult(
                command_id=command.command_id,
                external_reference=(
                    f"{self._configuration.external_reference_prefix}"
                    f"{self._state_version}"
                ),
                provider_state_revision=self._state_revision,
            )
            self._executions[command.idempotency_key] = (
                command.parameter_sha256,
                result,
            )
            return result

    def verify(self, command: MediaCommand, result: MediaActionResult) -> bool:
        with self._lock:
            self._require_ready(command.provider_connection_id)
            state = self._require_profile(command.profile_id)
            self._require_episodes(command.episode_ids)
            self._delay()
            prior = self._executions.get(command.idempotency_key)
            if prior is None or prior[1].command_id != result.command_id:
                raise ValueError(MediaProviderErrorMessages.RESULT_NOT_RECOGNIZED)
            if (
                self._configuration.fail_verification
                or self._configuration.stale_writes
            ):
                return False
            episode_id = command.episode_ids[0]
            if command.kind is MediaCommandKind.MARK_WATCHED:
                return episode_id in state.watched_episode_ids
            if command.kind is MediaCommandKind.SET_FAVORITE:
                return (episode_id in state.favorite_episode_ids) is command.favorite
            if command.kind is MediaCommandKind.CREATE_PLAYLIST:
                return state.playlists.get(command.playlist_name) == command.episode_ids
            return any(
                request.command_id == command.command_id
                and request.episode_id == episode_id
                for request in state.playback_requests
            )

    def set_available(self, available: bool) -> None:
        with self._lock:
            self._available = available

    def advance_connection_revision(self, revision: str) -> None:
        if not revision or revision.strip() != revision:
            raise ValueError(MediaProviderErrorMessages.MOCK_CONFIGURATION_INVALID)
        with self._lock:
            self._connection_revision = revision

    @property
    def _state_revision(self) -> str:
        return f"{self._configuration.state_revision_prefix}{self._state_version}"

    def _apply(self, command: MediaCommand, state: _MutableProfileState) -> None:
        episode_id = command.episode_ids[0]
        if command.kind is MediaCommandKind.MARK_WATCHED:
            state.watched_episode_ids.add(episode_id)
        elif command.kind is MediaCommandKind.SET_FAVORITE:
            target = state.favorite_episode_ids
            target.add(episode_id) if command.favorite else target.discard(episode_id)
        elif command.kind is MediaCommandKind.CREATE_PLAYLIST:
            assert command.playlist_name is not None
            state.playlists[command.playlist_name] = command.episode_ids
        else:
            state.playback_requests.append(
                MediaPlaybackRequest(command.command_id, episode_id)
            )

    def _require_connection(self, provider_connection_id: UUID) -> None:
        if provider_connection_id != self._connection_id:
            raise ValueError(MediaProviderErrorMessages.CONNECTION_NOT_FOUND)

    def _require_ready(self, provider_connection_id: UUID) -> None:
        self._require_connection(provider_connection_id)
        if not self._available:
            raise ConnectionError(MediaProviderErrorMessages.PROVIDER_UNAVAILABLE)

    def _require_profile(self, profile_id: UUID) -> _MutableProfileState:
        state = self._profiles.get(profile_id)
        if state is None:
            raise PermissionError(MediaProviderErrorMessages.PROFILE_NOT_AUTHORIZED)
        return state

    def _require_episodes(self, episode_ids: tuple[UUID, ...]) -> None:
        if any(episode_id not in self._episodes for episode_id in episode_ids):
            raise ValueError(MediaProviderErrorMessages.EPISODE_NOT_FOUND)

    def _delay(self) -> None:
        if self._configuration.latency_seconds:
            self._sleeper(self._configuration.latency_seconds)

    @staticmethod
    def _validate_seed(seed: MockMediaProviderSeed) -> None:
        episode_ids = tuple(episode.episode_id for episode in seed.episodes)
        profile_ids = tuple(profile.profile_id for profile in seed.profiles)
        known_episode_ids = set(episode_ids)
        invalid_profile_state = any(
            not (
                profile.watched_episode_ids | profile.favorite_episode_ids
            ).issubset(known_episode_ids)
            for profile in seed.profiles
        )
        if (
            not isinstance(seed.connection_id, UUID)
            or not episode_ids
            or not profile_ids
            or len(set(episode_ids)) != len(episode_ids)
            or len(set(profile_ids)) != len(profile_ids)
            or invalid_profile_state
        ):
            raise ValueError(MediaProviderErrorMessages.MOCK_SEED_INVALID)
