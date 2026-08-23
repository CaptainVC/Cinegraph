from dataclasses import replace
from uuid import UUID

import httpx
import pytest
from tests.contracts.media_provider_contract import (
    MediaProviderContractContext,
    assert_media_provider_contract,
)

from cinegraph.adapters.media import JellyfinMediaProvider
from cinegraph.application.models.media_provider import MediaProviderEpisode
from cinegraph.common.error_messages import MediaProviderErrorMessages
from cinegraph.config import (
    DEFAULT_JELLYFIN_PROVIDER_CONFIGURATION,
    JellyfinConnectionSettings,
    JellyfinEpisodeMapping,
)

CONNECTION_ID = UUID(int=901)
PROFILE_ID = UUID(int=902)
OWNER_ID = UUID(int=903)
EPISODE_ONE_ID = UUID(int=904)
EPISODE_TWO_ID = UUID(int=905)
EPISODES = (
    MediaProviderEpisode(EPISODE_ONE_ID, "jf-item-1", "Synthetic Pilot"),
    MediaProviderEpisode(EPISODE_TWO_ID, "jf-item-2", "Synthetic Follow-up"),
)
ACCESS_TOKEN = "test-only-jellyfin-token"


class StatefulJellyfinServer:
    def __init__(self) -> None:
        self.watched: set[str] = set()
        self.favorites: set[str] = set()
        self.playlists: dict[str, tuple[str, tuple[str, ...]]] = {}
        self.now_playing: str | None = None
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert ACCESS_TOKEN in request.headers["Authorization"]
        assert ACCESS_TOKEN not in str(request.url)
        path = request.url.path
        method = request.method
        if path == "/System/Info/Public":
            return self._json(
                {"Id": "server-1", "Version": "10.11.0", "ServerName": "Test"}
            )
        if path == "/Items":
            if request.url.params.get("includeItemTypes") == "Playlist":
                return self._json(
                    {
                        "Items": [
                            {"Id": playlist_id, "Name": value[0]}
                            for playlist_id, value in self.playlists.items()
                        ]
                    }
                )
            requested_ids = request.url.params.get_list("ids")
            item_ids = requested_ids or ["jf-item-1", "jf-item-2"]
            return self._json({"Items": [self._episode(item_id) for item_id in item_ids]})
        if path.startswith("/UserItems/") and path.endswith("/UserData"):
            item_id = path.split("/")[2]
            return self._json(self._user_data(item_id))
        if path.startswith("/UserPlayedItems/") and method == "POST":
            item_id = path.rsplit("/", 1)[-1]
            self.watched.add(item_id)
            return self._json(self._user_data(item_id))
        if path.startswith("/UserFavoriteItems/"):
            item_id = path.rsplit("/", 1)[-1]
            if method == "POST":
                self.favorites.add(item_id)
            else:
                self.favorites.discard(item_id)
            return self._json(self._user_data(item_id))
        if path == "/Playlists" and method == "POST":
            playlist_id = f"playlist-{len(self.playlists) + 1}"
            self.playlists[playlist_id] = (
                request.url.params["name"],
                tuple(request.url.params.get_list("ids")),
            )
            return self._json({"Id": playlist_id})
        if path.startswith("/Playlists/") and path.endswith("/Items"):
            playlist_id = path.split("/")[2]
            item_ids = self.playlists[playlist_id][1]
            return self._json({"Items": [self._episode(item_id) for item_id in item_ids]})
        if path == "/Sessions/session-1/Playing" and method == "POST":
            self.now_playing = request.url.params.get_list("itemIds")[0]
            return httpx.Response(204)
        if path == "/Sessions" and method == "GET":
            return self._json(
                [
                    {
                        "Id": "session-1",
                        "NowPlayingItem": {"Id": self.now_playing},
                    }
                ]
            )
        return httpx.Response(404, json={"error": f"unhandled {method} {path}"})

    def _episode(self, item_id: str) -> dict[str, object]:
        title = "Synthetic Pilot" if item_id == "jf-item-1" else "Synthetic Follow-up"
        return {"Id": item_id, "Name": title, "UserData": self._user_data(item_id)}

    def _user_data(self, item_id: str) -> dict[str, object]:
        return {
            "Played": item_id in self.watched,
            "IsFavorite": item_id in self.favorites,
        }

    @staticmethod
    def _json(payload: object) -> httpx.Response:
        return httpx.Response(200, json=payload)


def connection() -> JellyfinConnectionSettings:
    return JellyfinConnectionSettings(
        connection_id=CONNECTION_ID,
        profile_id=PROFILE_ID,
        user_id="jellyfin-user-1",
        base_url="https://jellyfin.test",
        access_token=ACCESS_TOKEN,
        device_id="cinegraph-tests",
        playback_session_id="session-1",
        episode_mappings=tuple(
            JellyfinEpisodeMapping(item.episode_id, item.provider_item_id, item.title)
            for item in EPISODES
        ),
    )


def test_jellyfin_adapter_passes_reusable_provider_contract() -> None:
    server = StatefulJellyfinServer()
    client = httpx.Client(transport=httpx.MockTransport(server))
    provider = JellyfinMediaProvider(connection(), client=client)

    assert_media_provider_contract(
        provider,
        MediaProviderContractContext(
            connection_id=CONNECTION_ID,
            profile_id=PROFILE_ID,
            provider_owner_user_id=OWNER_ID,
            episodes=EPISODES,
        ),
    )

    assert not provider.health(CONNECTION_ID).simulated
    assert server.watched == {"jf-item-1"}
    assert server.favorites == {"jf-item-2"}
    assert server.now_playing == "jf-item-1"


def test_jellyfin_adapter_retries_then_opens_circuit() -> None:
    calls = []
    delays = []

    def unavailable(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503, json={"error": "unavailable"})

    configuration = replace(
        DEFAULT_JELLYFIN_PROVIDER_CONFIGURATION,
        maximum_attempts=2,
        retry_backoff_seconds=0.1,
        circuit_failure_threshold=1,
    )
    provider = JellyfinMediaProvider(
        connection(),
        configuration,
        httpx.Client(transport=httpx.MockTransport(unavailable)),
        sleeper=delays.append,
        monotonic=lambda: 10.0,
    )

    with pytest.raises(
        ConnectionError,
        match=MediaProviderErrorMessages.PROVIDER_REQUEST_FAILED,
    ):
        provider.connection_revision(CONNECTION_ID)
    assert len(calls) == 2
    assert delays == [0.1]

    with pytest.raises(
        ConnectionError,
        match=MediaProviderErrorMessages.PROVIDER_CIRCUIT_OPEN,
    ):
        provider.connection_revision(CONNECTION_ID)
    assert len(calls) == 2


def test_jellyfin_adapter_fails_closed_on_auth_and_mapping_errors() -> None:
    auth_provider = JellyfinMediaProvider(
        connection(),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(401, json={"error": "no"})
            )
        ),
    )
    with pytest.raises(
        PermissionError,
        match=MediaProviderErrorMessages.PROVIDER_AUTHENTICATION_FAILED,
    ):
        auth_provider.connection_revision(CONNECTION_ID)

    server = StatefulJellyfinServer()

    def incomplete(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Items":
            return httpx.Response(200, json={"Items": [server._episode("jf-item-1")]})
        return server(request)

    provider = JellyfinMediaProvider(
        connection(),
        client=httpx.Client(transport=httpx.MockTransport(incomplete)),
    )
    with pytest.raises(
        ValueError,
        match=MediaProviderErrorMessages.EPISODE_MAPPING_INCOMPLETE,
    ):
        provider.list_library(CONNECTION_ID, PROFILE_ID)


def test_jellyfin_connection_repr_redacts_token_and_requires_https() -> None:
    assert ACCESS_TOKEN not in repr(connection())

    with pytest.raises(
        ValueError,
        match=MediaProviderErrorMessages.CONNECTION_CONFIGURATION_INVALID,
    ):
        replace(connection(), base_url="http://jellyfin.test")
