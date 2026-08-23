import hashlib
import json
import time
from collections.abc import Callable
from threading import RLock
from typing import Any
from uuid import UUID

import httpx

from cinegraph.application.models.media_action import MediaActionResult
from cinegraph.application.models.media_provider import (
    MediaPlaybackRequest,
    MediaProviderEpisode,
    MediaProviderHealth,
    MediaProviderPlaylist,
    MediaProviderProfileSnapshot,
)
from cinegraph.common.error_messages import (
    MediaActionErrorMessages,
    MediaProviderErrorMessages,
)
from cinegraph.config import (
    DEFAULT_JELLYFIN_PROVIDER_CONFIGURATION,
    JellyfinConnectionSettings,
    JellyfinProviderConfiguration,
)
from cinegraph.config.jellyfin import (
    JELLYFIN_EPISODE_ITEM_TYPE,
    JELLYFIN_FAVORITE_ITEMS_PATH,
    JELLYFIN_ITEMS_PATH,
    JELLYFIN_PLAY_NOW_COMMAND,
    JELLYFIN_PLAYED_ITEMS_PATH,
    JELLYFIN_PLAYLIST_ITEM_TYPE,
    JELLYFIN_PLAYLIST_ITEMS_PATH,
    JELLYFIN_PLAYLISTS_PATH,
    JELLYFIN_PUBLIC_SYSTEM_INFO_PATH,
    JELLYFIN_SESSION_PLAY_PATH,
    JELLYFIN_SESSIONS_PATH,
    JELLYFIN_UNAVAILABLE_REVISION,
    JELLYFIN_USER_DATA_PATH,
    JELLYFIN_VIDEO_MEDIA_TYPE,
)
from cinegraph.domain.enums.enum import MediaCommandKind
from cinegraph.domain.models.media_action import MediaCommand


class JellyfinMediaProvider:
    """HTTP adapter for one explicitly mapped Jellyfin user and library."""

    def __init__(
        self,
        connection: JellyfinConnectionSettings,
        configuration: JellyfinProviderConfiguration = (
            DEFAULT_JELLYFIN_PROVIDER_CONFIGURATION
        ),
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._connection = connection
        self._configuration = configuration
        self._client = client or httpx.Client(timeout=configuration.timeout_seconds)
        self._owns_client = client is None
        self._sleeper = sleeper
        self._monotonic = monotonic
        self._mapping = {
            mapping.episode_id: mapping for mapping in connection.episode_mappings
        }
        self._reverse_mapping = {
            mapping.provider_item_id: mapping
            for mapping in connection.episode_mappings
        }
        self._executions: dict[str, tuple[str, MediaActionResult]] = {}
        self._verified_playback_requests: list[MediaPlaybackRequest] = []
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0
        self._last_revision = JELLYFIN_UNAVAILABLE_REVISION
        self._lock = RLock()

    def __enter__(self) -> "JellyfinMediaProvider":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def health(self, provider_connection_id: UUID) -> MediaProviderHealth:
        self._require_connection(provider_connection_id)
        try:
            info = self._system_info()
            revision = self._revision(info)
            self._last_revision = revision
            label = f"{self._configuration.provider_label} ({info['ServerName']})"
            return MediaProviderHealth(label, revision, True, False)
        except (ConnectionError, PermissionError, RuntimeError, ValueError):
            return MediaProviderHealth(
                self._configuration.provider_label,
                self._last_revision,
                False,
                False,
            )

    def connection_revision(self, provider_connection_id: UUID) -> str:
        self._require_connection(provider_connection_id)
        revision = self._revision(self._system_info())
        self._last_revision = revision
        return revision

    def list_library(
        self,
        provider_connection_id: UUID,
        profile_id: UUID,
    ) -> tuple[MediaProviderEpisode, ...]:
        self._require_connection(provider_connection_id)
        self._require_profile(profile_id)
        payloads = self._episode_payloads()
        episodes = tuple(
            MediaProviderEpisode(
                mapping.episode_id,
                mapping.provider_item_id,
                str(payload["Name"]),
            )
            for mapping, payload in payloads
        )
        return tuple(sorted(episodes, key=lambda episode: episode.episode_id))

    def profile_snapshot(
        self,
        provider_connection_id: UUID,
        profile_id: UUID,
    ) -> MediaProviderProfileSnapshot:
        self._require_connection(provider_connection_id)
        self._require_profile(profile_id)
        episode_payloads = self._episode_payloads()
        watched = frozenset(
            mapping.episode_id
            for mapping, payload in episode_payloads
            if self._user_data(payload).get("Played") is True
        )
        favorites = frozenset(
            mapping.episode_id
            for mapping, payload in episode_payloads
            if self._user_data(payload).get("IsFavorite") is True
        )
        playlists = self._playlists()
        revision = self._payload_revision(
            {
                "favorites": sorted(str(value) for value in favorites),
                "playlists": [
                    [item.name, [str(value) for value in item.episode_ids]]
                    for item in playlists
                ],
                "watched": sorted(str(value) for value in watched),
            }
        )
        return MediaProviderProfileSnapshot(
            profile_id=profile_id,
            watched_episode_ids=watched,
            favorite_episode_ids=favorites,
            playlists=playlists,
            playback_requests=tuple(self._verified_playback_requests),
            state_revision=revision,
        )

    def execute(self, command: MediaCommand) -> MediaActionResult:
        with self._lock:
            self._require_command(command)
            current_revision = self.connection_revision(
                command.provider_connection_id
            )
            if current_revision != command.provider_connection_revision:
                raise ValueError(
                    MediaActionErrorMessages.PROVIDER_CONNECTION_CHANGED
                )
            existing = self._executions.get(command.idempotency_key)
            if existing is not None:
                command_sha256, result = existing
                if command_sha256 != command.parameter_sha256:
                    raise ValueError(MediaProviderErrorMessages.IDEMPOTENCY_KEY_REUSED)
                return MediaActionResult(
                    result.command_id,
                    result.external_reference,
                    result.provider_state_revision,
                    idempotent_replay=True,
                )
            result = self._execute_once(command)
            self._executions[command.idempotency_key] = (
                command.parameter_sha256,
                result,
            )
            return result

    def verify(self, command: MediaCommand, result: MediaActionResult) -> bool:
        with self._lock:
            self._require_command(command)
            known = self._executions.get(command.idempotency_key)
            if known is None or known[1] != result:
                raise ValueError(MediaProviderErrorMessages.RESULT_NOT_RECOGNIZED)
            item_id = self._mapping[command.episode_ids[0]].provider_item_id
            if command.kind in {
                MediaCommandKind.MARK_WATCHED,
                MediaCommandKind.SET_FAVORITE,
            }:
                data = self._request_json(
                    "GET",
                    JELLYFIN_USER_DATA_PATH.format(item_id=item_id),
                    params={"userId": self._connection.user_id},
                    retry_safe=True,
                )
                if command.kind is MediaCommandKind.MARK_WATCHED:
                    return data.get("Played") is True
                return data.get("IsFavorite") is command.favorite
            if command.kind is MediaCommandKind.CREATE_PLAYLIST:
                payload = self._request_json(
                    "GET",
                    JELLYFIN_PLAYLIST_ITEMS_PATH.format(
                        playlist_id=result.external_reference
                    ),
                    params={"userId": self._connection.user_id},
                    retry_safe=True,
                )
                return self._canonical_ids(payload) == command.episode_ids
            verified = self._verify_playback(item_id)
            if verified and not any(
                request.command_id == command.command_id
                for request in self._verified_playback_requests
            ):
                self._verified_playback_requests.append(
                    MediaPlaybackRequest(command.command_id, command.episode_ids[0])
                )
            return verified

    def _execute_once(self, command: MediaCommand) -> MediaActionResult:
        item_ids = [
            self._mapping[episode_id].provider_item_id
            for episode_id in command.episode_ids
        ]
        if command.kind is MediaCommandKind.MARK_WATCHED:
            payload = self._request_json(
                "POST",
                JELLYFIN_PLAYED_ITEMS_PATH.format(item_id=item_ids[0]),
                params={"userId": self._connection.user_id},
                retry_safe=True,
            )
            reference = item_ids[0]
        elif command.kind is MediaCommandKind.SET_FAVORITE:
            payload = self._request_json(
                "POST" if command.favorite else "DELETE",
                JELLYFIN_FAVORITE_ITEMS_PATH.format(item_id=item_ids[0]),
                params={"userId": self._connection.user_id},
                retry_safe=True,
            )
            reference = item_ids[0]
        elif command.kind is MediaCommandKind.CREATE_PLAYLIST:
            payload = self._request_json(
                "POST",
                JELLYFIN_PLAYLISTS_PATH,
                params={
                    "name": command.playlist_name,
                    "ids": item_ids,
                    "userId": self._connection.user_id,
                    "mediaType": JELLYFIN_VIDEO_MEDIA_TYPE,
                },
                retry_safe=False,
            )
            reference = self._required_text(payload, "Id")
        else:
            session_id = self._connection.playback_session_id
            if session_id is None:
                raise ValueError(MediaProviderErrorMessages.PLAYBACK_SESSION_REQUIRED)
            self._request(
                "POST",
                JELLYFIN_SESSION_PLAY_PATH.format(session_id=session_id),
                params={
                    "playCommand": JELLYFIN_PLAY_NOW_COMMAND,
                    "itemIds": item_ids,
                },
                retry_safe=False,
            )
            payload = {"session_id": session_id, "item_id": item_ids[0]}
            reference = f"{session_id}:{item_ids[0]}"
        return MediaActionResult(
            command_id=command.command_id,
            external_reference=reference,
            provider_state_revision=self._payload_revision(payload),
        )

    def _episode_payloads(self) -> tuple[tuple[Any, dict[str, object]], ...]:
        payload = self._request_json(
            "GET",
            JELLYFIN_ITEMS_PATH,
            params={
                "userId": self._connection.user_id,
                "ids": [
                    mapping.provider_item_id
                    for mapping in self._connection.episode_mappings
                ],
                "includeItemTypes": JELLYFIN_EPISODE_ITEM_TYPE,
                "recursive": "true",
                "enableUserData": "true",
            },
            retry_safe=True,
        )
        items = self._items(payload)
        by_id = {self._required_text(item, "Id"): item for item in items}
        if set(by_id) != set(self._reverse_mapping):
            raise ValueError(MediaProviderErrorMessages.EPISODE_MAPPING_INCOMPLETE)
        return tuple(
            (mapping, by_id[mapping.provider_item_id])
            for mapping in self._connection.episode_mappings
        )

    def _playlists(self) -> tuple[MediaProviderPlaylist, ...]:
        payload = self._request_json(
            "GET",
            JELLYFIN_ITEMS_PATH,
            params={
                "userId": self._connection.user_id,
                "includeItemTypes": JELLYFIN_PLAYLIST_ITEM_TYPE,
                "recursive": "true",
            },
            retry_safe=True,
        )
        playlists = []
        for playlist in self._items(payload):
            playlist_id = self._required_text(playlist, "Id")
            name = self._required_text(playlist, "Name")
            entries = self._request_json(
                "GET",
                JELLYFIN_PLAYLIST_ITEMS_PATH.format(playlist_id=playlist_id),
                params={"userId": self._connection.user_id},
                retry_safe=True,
            )
            provider_ids = tuple(
                self._required_text(item, "Id") for item in self._items(entries)
            )
            if provider_ids and all(
                provider_id in self._reverse_mapping for provider_id in provider_ids
            ):
                playlists.append(
                    MediaProviderPlaylist(
                        name,
                        tuple(
                            self._reverse_mapping[provider_id].episode_id
                            for provider_id in provider_ids
                        ),
                    )
                )
        return tuple(sorted(playlists, key=lambda playlist: playlist.name))

    def _verify_playback(self, item_id: str) -> bool:
        session_id = self._connection.playback_session_id
        if session_id is None:
            return False
        payload = self._request_json(
            "GET",
            JELLYFIN_SESSIONS_PATH,
            params={"controllableByUserId": self._connection.user_id},
            retry_safe=True,
        )
        sessions = payload if isinstance(payload, list) else []
        return any(
            isinstance(session, dict)
            and session.get("Id") == session_id
            and isinstance(session.get("NowPlayingItem"), dict)
            and session["NowPlayingItem"].get("Id") == item_id
            for session in sessions
        )

    def _system_info(self) -> dict[str, object]:
        payload = self._request_json(
            "GET",
            JELLYFIN_PUBLIC_SYSTEM_INFO_PATH,
            retry_safe=True,
        )
        self._required_text(payload, "Id")
        self._required_text(payload, "Version")
        self._required_text(payload, "ServerName")
        return payload

    def _revision(self, info: dict[str, object]) -> str:
        return self._payload_revision(
            {
                "base_url": self._connection.base_url,
                "episode_mappings": [
                    [str(mapping.episode_id), mapping.provider_item_id]
                    for mapping in self._connection.episode_mappings
                ],
                "playback_session_id": self._connection.playback_session_id,
                "server_id": info["Id"],
                "server_version": info["Version"],
                "user_id": self._connection.user_id,
            }
        )

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        retry_safe: bool,
    ) -> dict[str, object] | list[object]:
        response = self._request(
            method,
            path,
            params=params,
            retry_safe=retry_safe,
        )
        try:
            payload = response.json()
        except ValueError as error:
            raise ValueError(
                MediaProviderErrorMessages.PROVIDER_RESPONSE_INVALID
            ) from error
        if not isinstance(payload, (dict, list)):
            raise ValueError(MediaProviderErrorMessages.PROVIDER_RESPONSE_INVALID)
        return payload

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        retry_safe: bool,
    ) -> httpx.Response:
        self._require_circuit_closed()
        maximum_attempts = (
            self._configuration.maximum_attempts if retry_safe else 1
        )
        last_error: Exception | None = None
        for attempt in range(maximum_attempts):
            try:
                response = self._client.request(
                    method,
                    f"{self._connection.base_url}{path}",
                    params=params,
                    headers=self._headers,
                    timeout=self._configuration.timeout_seconds,
                )
            except httpx.RequestError as error:
                last_error = error
            else:
                if response.status_code in {401, 403}:
                    raise PermissionError(
                        MediaProviderErrorMessages.PROVIDER_AUTHENTICATION_FAILED
                    )
                if response.status_code not in (
                    self._configuration.retryable_status_codes
                ):
                    if response.is_error:
                        raise RuntimeError(
                            MediaProviderErrorMessages.PROVIDER_REQUEST_FAILED
                        )
                    self._consecutive_failures = 0
                    return response
                last_error = RuntimeError(
                    MediaProviderErrorMessages.PROVIDER_REQUEST_FAILED
                )
            if attempt + 1 < maximum_attempts:
                self._sleeper(
                    self._configuration.retry_backoff_seconds * (2**attempt)
                )
        self._record_transient_failure()
        raise ConnectionError(
            MediaProviderErrorMessages.PROVIDER_REQUEST_FAILED
        ) from last_error

    @property
    def _headers(self) -> dict[str, str]:
        authorization = (
            f'MediaBrowser Client="{self._configuration.client_name}", '
            f'Device="{self._configuration.device_name}", '
            f'DeviceId="{self._connection.device_id}", '
            f'Version="{self._configuration.client_version}", '
            f'Token="{self._connection.access_token}"'
        )
        return {"Accept": "application/json", "Authorization": authorization}

    def _record_transient_failure(self) -> None:
        self._consecutive_failures += 1
        if (
            self._consecutive_failures
            >= self._configuration.circuit_failure_threshold
        ):
            self._circuit_open_until = (
                self._monotonic() + self._configuration.circuit_recovery_seconds
            )

    def _require_circuit_closed(self) -> None:
        if self._monotonic() < self._circuit_open_until:
            raise ConnectionError(MediaProviderErrorMessages.PROVIDER_CIRCUIT_OPEN)

    def _require_connection(self, provider_connection_id: UUID) -> None:
        if provider_connection_id != self._connection.connection_id:
            raise ValueError(MediaProviderErrorMessages.CONNECTION_NOT_FOUND)

    def _require_profile(self, profile_id: UUID) -> None:
        if profile_id != self._connection.profile_id:
            raise PermissionError(MediaProviderErrorMessages.PROFILE_NOT_AUTHORIZED)

    def _require_command(self, command: MediaCommand) -> None:
        self._require_connection(command.provider_connection_id)
        self._require_profile(command.profile_id)
        if any(episode_id not in self._mapping for episode_id in command.episode_ids):
            raise ValueError(MediaProviderErrorMessages.EPISODE_NOT_FOUND)

    @staticmethod
    def _items(payload: dict[str, object] | list[object]) -> tuple[dict[str, object], ...]:
        if not isinstance(payload, dict) or not isinstance(payload.get("Items"), list):
            raise ValueError(MediaProviderErrorMessages.PROVIDER_RESPONSE_INVALID)
        items = payload["Items"]
        if not all(isinstance(item, dict) for item in items):
            raise ValueError(MediaProviderErrorMessages.PROVIDER_RESPONSE_INVALID)
        return tuple(items)

    @staticmethod
    def _user_data(payload: dict[str, object]) -> dict[str, object]:
        user_data = payload.get("UserData")
        return user_data if isinstance(user_data, dict) else {}

    def _canonical_ids(
        self, payload: dict[str, object] | list[object]
    ) -> tuple[UUID, ...]:
        provider_ids = tuple(
            self._required_text(item, "Id") for item in self._items(payload)
        )
        if any(item_id not in self._reverse_mapping for item_id in provider_ids):
            return ()
        return tuple(
            self._reverse_mapping[item_id].episode_id for item_id in provider_ids
        )

    @staticmethod
    def _required_text(payload: dict[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(MediaProviderErrorMessages.PROVIDER_RESPONSE_INVALID)
        return value

    @staticmethod
    def _payload_revision(payload: object) -> str:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
