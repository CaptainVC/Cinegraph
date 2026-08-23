from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from cinegraph.adapters.source.tvmaze_series_metadata_provider import (
    TVMazeSeriesMetadataProvider,
)
from cinegraph.domain.exceptions.tvmaze_errors import (
    TVMazeEpisodeReconciliationError,
    TVMazeProviderError,
    TVMazeShowMismatchError,
)
from cinegraph.domain.models.watch_state import EpisodePosition, EpisodeRef


class _Clock:
    def now_utc(self):
        return datetime(2026, 1, 1, tzinfo=UTC)


def test_maps_show_regular_cast_episode_guest_cast_and_preserves_provider_order():
    series_id = UUID("00000000-0000-0000-0000-000000000001")
    episode = EpisodeRef(
        series_id,
        UUID("00000000-0000-0000-0000-000000000002"),
        UUID("00000000-0000-0000-0000-000000000003"),
        EpisodePosition(1, 1),
    )
    payloads = {
        "/shows/80": {
            "id": 80,
            "name": "Modern Family",
            "url": "https://www.tvmaze.com/shows/80/modern-family",
            "image": {
                "medium": "https://static.tvmaze.com/uploads/images/medium_portrait/1/1.jpg",
                "original": "https://static.tvmaze.com/uploads/images/original_untouched/1/1.jpg",
            },
        },
        "/shows/80/cast": [
            {
                "person": {
                    "id": 1,
                    "name": "A",
                    "url": "https://www.tvmaze.com/people/1/a",
                },
                "character": {
                    "id": 2,
                    "name": "C",
                    "url": "https://www.tvmaze.com/characters/2/c",
                },
            }
        ],
        "/shows/80/seasons": [{"id": 101, "number": 1}, {"id": 102, "number": 2}],
        "/seasons/101/episodes": [
            {
                "id": 10,
                "season": 1,
                "number": 1,
                "name": "Pilot",
                "url": "https://www.tvmaze.com/episodes/10/pilot",
                "_embedded": {
                    "guestcast": [
                        {
                            "person": {
                                "id": 3,
                                "name": "G",
                                "url": "https://www.tvmaze.com/people/3/g",
                            },
                            "character": {
                                "id": 4,
                                "name": "D",
                                "url": "https://www.tvmaze.com/characters/4/d",
                            },
                        }
                    ]
                },
            }
        ],
    }

    requests = []

    def handler(request: httpx.Request):
        requests.append(str(request.url))
        return httpx.Response(200, json=payloads[request.url.path])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = TVMazeSeriesMetadataProvider(client, _Clock()).fetch(
            provider_show_id=80,
            expected_title="modern family",
            series_id=series_id,
            episodes=(episode,),
        )
    assert result.poster is not None
    assert (
        result.poster.provider_asset_id == "/uploads/images/original_untouched/1/1.jpg"
    )
    assert result.regular_cast[0].credit_kind.value == "regular"
    assert result.episodes[0].guest_cast[0].credit_kind.value == "guest"
    assert result.episodes[0].provider_episode_id == 10
    assert any(
        item.endswith("/seasons/101/episodes?embed=guestcast") for item in requests
    )
    assert not any("/seasons/102/" in item for item in requests)


def test_no_poster_is_supported():
    series_id = UUID("00000000-0000-0000-0000-000000000001")
    episode = EpisodeRef(
        series_id,
        UUID("00000000-0000-0000-0000-000000000002"),
        UUID("00000000-0000-0000-0000-000000000003"),
        EpisodePosition(1, 1),
    )
    payloads = {
        "/shows/80": {
            "id": 80,
            "name": "Modern Family",
            "url": "https://www.tvmaze.com/shows/80/modern-family",
            "image": None,
        },
        "/shows/80/cast": [],
        "/shows/80/seasons": [{"id": 101, "number": 1}],
        "/seasons/101/episodes": [
            {
                "id": 10,
                "season": 1,
                "number": 1,
                "name": "Pilot",
                "url": "https://www.tvmaze.com/episodes/10/pilot",
                "_embedded": {"guestcast": []},
            }
        ],
    }

    def handler(request: httpx.Request):
        return httpx.Response(200, json=payloads[request.url.path])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = TVMazeSeriesMetadataProvider(client, _Clock()).fetch(
            provider_show_id=80,
            expected_title="Modern Family",
            series_id=series_id,
            episodes=(episode,),
        )
    assert result.poster is None


def test_show_id_and_title_mismatch_are_typed_errors():
    episode = EpisodeRef(UUID(int=1), UUID(int=2), UUID(int=3), EpisodePosition(1, 1))

    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            json={
                "id": 81,
                "name": "Other Show",
                "url": "https://www.tvmaze.com/shows/81/other",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = TVMazeSeriesMetadataProvider(client, _Clock())
        with pytest.raises(TVMazeProviderError):
            provider.fetch(
                provider_show_id=80,
                expected_title="Modern Family",
                series_id=UUID(int=1),
                episodes=(episode,),
            )

    def title_handler(request: httpx.Request):
        return httpx.Response(
            200,
            json={
                "id": 80,
                "name": "Other Show",
                "url": "https://www.tvmaze.com/shows/80/other",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(title_handler)) as client:
        provider = TVMazeSeriesMetadataProvider(client, _Clock())
        with pytest.raises(TVMazeShowMismatchError):
            provider.fetch(
                provider_show_id=80,
                expected_title="Modern Family",
                series_id=UUID(int=1),
                episodes=(episode,),
            )


@pytest.mark.parametrize(
    ("episodes", "series_id"),
    [
        ((), UUID(int=1)),
        (
            (
                EpisodeRef(
                    UUID(int=2),
                    UUID(int=3),
                    UUID(int=4),
                    EpisodePosition(1, 1),
                ),
            ),
            UUID(int=1),
        ),
        (
            (
                EpisodeRef(
                    UUID(int=1),
                    UUID(int=2),
                    UUID(int=3),
                    EpisodePosition(1, 1),
                ),
            )
            * 2,
            UUID(int=1),
        ),
    ],
)
def test_invalid_episode_scopes_fail_before_network(episodes, series_id):
    def handler(_: httpx.Request):
        raise AssertionError("invalid input must fail before an HTTP request")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = TVMazeSeriesMetadataProvider(client, _Clock())
        with pytest.raises(ValueError):
            provider.fetch(
                provider_show_id=80,
                expected_title="Modern Family",
                series_id=series_id,
                episodes=episodes,
            )


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429),
        httpx.Response(200, json=[]),
        httpx.Response(
            200,
            json={
                "id": 80,
                "name": "Modern Family",
                "url": "https://untrusted.example/shows/80/modern-family",
            },
        ),
    ],
)
def test_transport_and_malformed_show_responses_are_typed(response):
    episode = EpisodeRef(UUID(int=1), UUID(int=2), UUID(int=3), EpisodePosition(1, 1))

    with httpx.Client(transport=httpx.MockTransport(lambda _: response)) as client:
        provider = TVMazeSeriesMetadataProvider(client, _Clock())
        with pytest.raises(TVMazeProviderError):
            provider.fetch(
                provider_show_id=80,
                expected_title="Modern Family",
                series_id=UUID(int=1),
                episodes=(episode,),
            )


@pytest.mark.parametrize(
    ("seasons", "season_episodes"),
    [
        ([], []),
        ([{"id": 101, "number": 1}, {"id": 102, "number": 1}], []),
        ([{"id": 101, "number": 1}], []),
        (
            [{"id": 101, "number": 1}],
            [
                {
                    "id": 10,
                    "season": 1,
                    "number": 1,
                    "name": "Pilot",
                    "url": "https://www.tvmaze.com/episodes/10/pilot",
                    "_embedded": {"guestcast": []},
                },
                {
                    "id": 11,
                    "season": 1,
                    "number": 1,
                    "name": "Pilot",
                    "url": "https://www.tvmaze.com/episodes/11/pilot",
                    "_embedded": {"guestcast": []},
                },
            ],
        ),
    ],
)
def test_missing_and_duplicate_provider_positions_fail_closed(
    seasons,
    season_episodes,
):
    episode = EpisodeRef(UUID(int=1), UUID(int=2), UUID(int=3), EpisodePosition(1, 1))
    payloads = {
        "/shows/80": {
            "id": 80,
            "name": "Modern Family",
            "url": "https://www.tvmaze.com/shows/80/modern-family",
            "image": None,
        },
        "/shows/80/cast": [],
        "/shows/80/seasons": seasons,
        "/seasons/101/episodes": season_episodes,
    }

    def handler(request: httpx.Request):
        return httpx.Response(200, json=payloads[request.url.path])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = TVMazeSeriesMetadataProvider(client, _Clock())
        with pytest.raises(TVMazeEpisodeReconciliationError):
            provider.fetch(
                provider_show_id=80,
                expected_title="Modern Family",
                series_id=UUID(int=1),
                episodes=(episode,),
            )
