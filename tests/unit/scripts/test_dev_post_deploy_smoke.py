import json
from collections.abc import Callable
from email.message import Message
from http.client import BadStatusLine, HTTPMessage
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest
from fastapi.testclient import TestClient
from scripts.dev_post_deploy_smoke import (
    AUTH_GUEST_PATH_SUFFIX,
    CATALOGUE_PATH_SUFFIX,
    CLIENT_CONFIG_PATH,
    DEFAULT_BASE_URL,
    EXPECTED_SERIES_ID,
    DevPostDeploySmoke,
    SmokeCheckError,
    normalize_base_url,
)
from tests.unit.adapters.api.test_fastapi_app import make_context

from cinegraph.adapters.api.fastapi_app import create_app
from cinegraph.adapters.catalogue.json_catalogue_manifest_loader import (
    JsonCatalogueManifestLoader,
)


class _Response:
    def __init__(self, body: object, *, status: int = 200, cookies: tuple[str, ...] = ()) -> None:
        self.status = status
        self.headers = HTTPMessage()
        for cookie in cookies:
            self.headers.add_header("Set-Cookie", cookie)
        self._body = json.dumps(body).encode() if not isinstance(body, bytes) else body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int = -1) -> bytes:
        return self._body

    def info(self) -> Message:
        return self.headers

    def getcode(self) -> int:
        return self.status


def _catalogue(*, extra_series: bool = False, extra_season: bool = False) -> dict[str, object]:
    seasons: list[dict[str, object]] = []
    for season_number in (1, 2):
        episodes = [
            {
                "episode_id": f"00000000-0000-0000-{season_number:04d}-{episode_number:012d}",
                "episode_number": episode_number,
                "episode_title": f"Episode {episode_number}",
                "guest_cast": [],
            }
            for episode_number in range(1, 25)
        ]
        seasons.append(
            {
                "season_id": f"00000000-0000-0000-0000-{season_number:012d}",
                "season_number": season_number,
                "episodes": episodes,
            }
        )
    if extra_season:
        seasons.append(
            {
                "season_id": "00000000-0000-0000-0000-000000000003",
                "season_number": 3,
                "episodes": [],
            }
        )
    series: list[dict[str, object]] = [
        {
            "series_id": EXPECTED_SERIES_ID,
            "series_name": "Modern Family",
            "seasons": seasons,
            "poster": None,
            "regular_cast": [],
            "metadata_source": None,
        },
    ]
    if extra_series:
        series.append({"series_name": "Other Series", "seasons": []})
    return {
        "schema_version": 1,
        "corpus_scope_revision": "guest-modern-family-s01-s02-v1",
        "series": series,
    }


def _open_sequence(
    catalogue: object | bytes | None = None,
    *,
    guest: object | bytes | None = None,
    client_config: object | bytes | None = None,
    api_prefix: str = "/api/v1",
    root_cookies: tuple[str, ...] = (
        "cinegraph_csrf=csrf-token; Path=/; SameSite=lax",
    ),
    guest_cookies: tuple[str, ...] = (
        "cinegraph_session=session-token; Path=/; HttpOnly",
    ),
) -> tuple[Callable[..., _Response], list[Request]]:
    requests: list[Request] = []
    responses = [
        _Response({}, cookies=root_cookies),
        _Response(
            client_config
            if client_config is not None
            else {
                "api_prefix": api_prefix,
                "agent_poll_interval_ms": 750,
                "agent_job_deadline_ms": 120_000,
            }
        ),
        _Response(
            guest
            if guest is not None
            else {
                "principal_kind": "guest",
                "corpus_scope_revision": "guest-modern-family-s01-s02-v1",
            },
            cookies=guest_cookies,
        ),
        _Response(catalogue if catalogue is not None else _catalogue()),
    ]

    def open_url(request: Request, **_kwargs: object) -> _Response:
        requests.append(request)
        return responses.pop(0)

    return open_url, requests


def test_success_uses_real_auth_cookie_and_never_calls_answer_endpoint() -> None:
    open_url, requests = _open_sequence()

    DevPostDeploySmoke(DEFAULT_BASE_URL, open_url).run()

    assert [request.full_url for request in requests] == [
        f"{DEFAULT_BASE_URL}/",
        f"{DEFAULT_BASE_URL}{CLIENT_CONFIG_PATH}",
        f"{DEFAULT_BASE_URL}/api/v1{AUTH_GUEST_PATH_SUFFIX}",
        f"{DEFAULT_BASE_URL}/api/v1{CATALOGUE_PATH_SUFFIX}",
    ]
    guest_request, catalogue_request = requests[2:]
    assert guest_request.get_method() == "POST"
    assert guest_request.get_header("Cookie") == "cinegraph_csrf=csrf-token"
    assert guest_request.get_header("X-csrf-token") == "csrf-token"
    assert catalogue_request.get_header("Cookie") == (
        "cinegraph_csrf=csrf-token; cinegraph_session=session-token"
    )
    assert all("answer" not in request.full_url and "agent" not in request.full_url for request in requests)


def test_uses_validated_runtime_api_prefix() -> None:
    open_url, requests = _open_sequence(api_prefix="/internal/api")

    DevPostDeploySmoke(DEFAULT_BASE_URL, open_url).run()

    assert [request.full_url for request in requests[2:]] == [
        f"{DEFAULT_BASE_URL}/internal/api{AUTH_GUEST_PATH_SUFFIX}",
        f"{DEFAULT_BASE_URL}/internal/api{CATALOGUE_PATH_SUFFIX}",
    ]


def test_actual_guest_catalogue_matches_the_smoke_schema_contract(tmp_path: Path) -> None:
    context, _ = make_context(tmp_path)
    context.catalogue = JsonCatalogueManifestLoader().load(
        Path("knowledge/catalogue.json")
    ).manifest
    with TestClient(create_app(context)) as client:
        guest = client.post("/api/v1/auth/guest")
        catalogue = client.get("/api/v1/catalogue")

    assert guest.status_code == 200
    assert catalogue.status_code == 200
    DevPostDeploySmoke(DEFAULT_BASE_URL)._validate_guest_catalogue(catalogue.json())


@pytest.mark.parametrize(
    "client_config",
    [
        b"not-json",
        {},
        {"api_prefix": 1},
        {"api_prefix": "api/v1"},
        {"api_prefix": "/api/v1/"},
        {"api_prefix": "/assets/api"},
        {"api_prefix": "/health"},
        {"api_prefix": "/client-config/private"},
        {"api_prefix": "/foo/../bar"},
    ],
)
def test_rejects_malformed_or_unsafe_runtime_api_prefix(
    client_config: object | bytes,
) -> None:
    open_url, _ = _open_sequence(client_config=client_config)

    with pytest.raises(SmokeCheckError):
        DevPostDeploySmoke(DEFAULT_BASE_URL, open_url).run()


@pytest.mark.parametrize("kwargs", [{"extra_series": True}, {"extra_season": True}])
def test_rejects_extra_guest_series_or_season(kwargs: dict[str, bool]) -> None:
    open_url, _ = _open_sequence(_catalogue(**kwargs))

    with pytest.raises(SmokeCheckError, match="unexpected"):
        DevPostDeploySmoke(DEFAULT_BASE_URL, open_url).run()


@pytest.mark.parametrize(
    "guest",
    [b"not-json", {"principal_kind": "authenticated"}, {"principal_kind": "guest"}],
)
def test_rejects_malformed_or_non_guest_auth_response(guest: object | bytes) -> None:
    open_url, _ = _open_sequence(guest=guest)

    with pytest.raises(SmokeCheckError):
        DevPostDeploySmoke(DEFAULT_BASE_URL, open_url).run()


def test_rejects_missing_session_cookie() -> None:
    open_url, _ = _open_sequence(guest_cookies=())

    with pytest.raises(SmokeCheckError, match="session cookie"):
        DevPostDeploySmoke(DEFAULT_BASE_URL, open_url).run()


@pytest.mark.parametrize(
    "root_cookies",
    [
        (),
        ("cinegraph_csrf=csrf-token; Path=/; Secure; SameSite=lax",),
        ("cinegraph_csrf=csrf-token; Path=/private; SameSite=lax",),
        ("cinegraph_csrf=csrf-token; Domain=example.com; Path=/; SameSite=lax",),
    ],
)
def test_rejects_missing_or_browser_inapplicable_csrf_cookie(
    root_cookies: tuple[str, ...],
) -> None:
    open_url, _ = _open_sequence(root_cookies=root_cookies)

    with pytest.raises(SmokeCheckError, match="CSRF cookie"):
        DevPostDeploySmoke(DEFAULT_BASE_URL, open_url).run()


@pytest.mark.parametrize(
    "guest_cookies",
    [
        ("cinegraph_session=session-token; Path=/; Secure; HttpOnly",),
        ("cinegraph_session=session-token; Path=/private; HttpOnly",),
        ("cinegraph_session=session-token; Domain=example.com; Path=/; HttpOnly",),
        ("cinegraph_session=session-token; Max-Age=0; Path=/; HttpOnly",),
    ],
)
def test_rejects_browser_inapplicable_session_cookie(
    guest_cookies: tuple[str, ...],
) -> None:
    open_url, _ = _open_sequence(guest_cookies=guest_cookies)

    with pytest.raises(SmokeCheckError, match="usable session cookie"):
        DevPostDeploySmoke(DEFAULT_BASE_URL, open_url).run()


@pytest.mark.parametrize(
    "catalogue",
    [
        b"not-json",
        {"series": {}},
        {
            "schema_version": True,
            "corpus_scope_revision": "guest-modern-family-s01-s02-v1",
            "series": [],
        },
        {
            **_catalogue(),
            "schema_version": 1.0,
        },
        {
            **_catalogue(),
            "corpus_scope_revision": "authenticated-all-v1",
        },
        {
            **_catalogue(),
            "series": [
                {
                    "series_id": "00000000-0000-0000-0000-000000000099",
                    "series_name": "Modern Family",
                    "seasons": [],
                    "poster": None,
                    "regular_cast": [],
                    "metadata_source": None,
                }
            ],
        },
        {
            **_catalogue(),
            "series": [
                {
                    "series_id": EXPECTED_SERIES_ID,
                    "series_name": "Modern Family",
                    "seasons": "1,2",
                    "poster": None,
                    "regular_cast": [],
                    "metadata_source": None,
                }
            ],
        },
        {
            **_catalogue(),
            "series": [
                {
                    "series_id": EXPECTED_SERIES_ID,
                    "series_name": "Modern Family",
                    "seasons": [
                        {"season_id": "bad", "season_number": "1", "episodes": []},
                        {"season_id": "bad", "season_number": 2, "episodes": []},
                    ],
                    "poster": None,
                    "regular_cast": [],
                    "metadata_source": None,
                }
            ],
        },
        {
            **_catalogue(),
            "unexpected": "field",
        },
        {
            **_catalogue(),
            "series": [
                {
                    **_catalogue()["series"][0],  # type: ignore[index]
                    "unexpected": "field",
                }
            ],
        },
    ],
)
def test_rejects_malformed_guest_catalogue(catalogue: object | bytes) -> None:
    open_url, _ = _open_sequence(catalogue)

    with pytest.raises(SmokeCheckError):
        DevPostDeploySmoke(DEFAULT_BASE_URL, open_url).run()


@pytest.mark.parametrize(
    "failure",
    [URLError("offline"), OSError("offline"), BadStatusLine("peer-secret")],
)
def test_rejects_network_failure_without_exposing_details(failure: Exception) -> None:
    def open_url(_request: Request, **_kwargs: object) -> None:
        raise failure

    with pytest.raises(SmokeCheckError, match="unavailable") as caught:
        DevPostDeploySmoke(DEFAULT_BASE_URL, open_url).run()
    assert "offline" not in str(caught.value)
    assert "peer-secret" not in str(caught.value)


def test_rejects_http_failure_without_reading_response_body() -> None:
    def open_url(request: Request, **_kwargs: object) -> None:
        raise HTTPError(request.full_url, 503, "unavailable", HTTPMessage(), None)

    with pytest.raises(SmokeCheckError, match="HTTP 503"):
        DevPostDeploySmoke(DEFAULT_BASE_URL, open_url).run()


@pytest.mark.parametrize(
    "base_url",
    [
        "https://127.0.0.1:18000",
        "http://127.0.0.2:18000",
        "http://127.0.0.1:18001",
        "http://127.0.0.1:18000/private",
        "http://127.0.0.1:18000?secret=1",
        "http://user:pass@127.0.0.1:18000",
    ],
)
def test_base_url_is_strictly_loopback_dev_origin(base_url: str) -> None:
    with pytest.raises(ValueError):
        normalize_base_url(base_url)


def test_base_url_normalization_accepts_only_optional_trailing_slash() -> None:
    assert normalize_base_url(DEFAULT_BASE_URL) == DEFAULT_BASE_URL
    assert normalize_base_url(f"{DEFAULT_BASE_URL}/") == DEFAULT_BASE_URL


def test_shell_runs_smoke_only_after_readiness_and_before_success() -> None:
    script = open("deploy/remote/deploy-dev.sh", encoding="utf-8").read()
    readiness = script.index('[[ "$ready" -eq 1 ]]')
    smoke = script.index("dev_post_deploy_smoke.py")
    success = script.index("deployment_succeeded=1")
    assert readiness < smoke < success
    assert '--base-url "http://127.0.0.1:$DEV_COMPOSE_PORT"' in script
    assert 'PYTHONPATH="$CURRENT_LINK"' in script
    smoke_script = open("scripts/dev_post_deploy_smoke.py", encoding="utf-8").read()
    assert 'CLIENT_CONFIG_PATH = "/client-config"' in smoke_script
    assert 'API_PREFIX = "/api/v1"' not in smoke_script
