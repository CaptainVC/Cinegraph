"""Run the secret-free, loopback-only Dev post-deploy smoke contract.

This check exercises only the public health-adjacent authentication and catalogue
boundary.  It deliberately never calls an answer, retrieval, or agent endpoint and
never prints an HTTP response body, cookie, token, or catalogue payload.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from http.client import HTTPException, HTTPResponse
from http.cookiejar import CookieJar
from http.cookies import CookieError, SimpleCookie
from typing import Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)
from uuid import UUID

DEV_LOOPBACK_HOST = "127.0.0.1"
DEV_PUBLISHED_PORT = 18_000
DEFAULT_BASE_URL = f"http://{DEV_LOOPBACK_HOST}:{DEV_PUBLISHED_PORT}"
ROOT_PATH = "/"
CLIENT_CONFIG_PATH = "/client-config"
AUTH_GUEST_PATH_SUFFIX = "/auth/guest"
CATALOGUE_PATH_SUFFIX = "/catalogue"
API_PREFIX_PATTERN = re.compile(r"/[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*")
RESERVED_API_PREFIX_ROOTS = ("/assets", "/health", CLIENT_CONFIG_PATH)
NONCANONICAL_API_PREFIX_SEGMENTS = frozenset({".", ".."})
SESSION_COOKIE_NAME = "cinegraph_session"
CSRF_COOKIE_NAME = "cinegraph_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"
EXPECTED_PRINCIPAL_KIND = "guest"
EXPECTED_SCOPE_REVISION = "guest-modern-family-s01-s02-v1"
EXPECTED_CATALOGUE_SCHEMA_VERSION = 1
EXPECTED_SERIES_ID = "00000000-0000-0000-0000-000000000011"
EXPECTED_SERIES_NAME = "Modern Family"
EXPECTED_SEASON_NUMBERS = frozenset({1, 2})
EXPECTED_EPISODE_NUMBERS = frozenset(range(1, 25))
CATALOGUE_FIELDS = frozenset({"schema_version", "corpus_scope_revision", "series"})
SERIES_FIELDS = frozenset(
    {"series_id", "series_name", "seasons", "poster", "regular_cast", "metadata_source"}
)
SEASON_FIELDS = frozenset({"season_id", "season_number", "episodes"})
EPISODE_FIELDS = frozenset(
    {"episode_id", "episode_number", "episode_title", "guest_cast"}
)
CREDIT_FIELDS = frozenset(
    {"name", "character_name", "credit_kind", "canonical_url", "character_canonical_url"}
)
POSTER_FIELDS = frozenset(
    {"url", "alt", "width", "height", "attribution", "license_name", "license_url"}
)
METADATA_SOURCE_FIELDS = frozenset(
    {"provider_name", "canonical_url", "attribution", "license_name", "license_url"}
)
REQUEST_TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class SmokeCheckError(RuntimeError):
    """A safe, operator-facing smoke-contract failure."""


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise SmokeCheckError("Dev smoke endpoint attempted an unexpected redirect")


@dataclass(frozen=True, slots=True)
class _HttpResponse:
    status: int
    body: bytes


class _ResponseProtocol(Protocol):
    status: int

    def read(self, limit: int = -1) -> bytes: ...

    def info(self): ...  # type: ignore[no-untyped-def]

    def __enter__(self) -> "_ResponseProtocol": ...

    def __exit__(self, *args: object) -> None: ...


class _OpenUrl(Protocol):
    def __call__(self, request: Request, *, timeout: int) -> _ResponseProtocol: ...


def normalize_base_url(base_url: str) -> str:
    """Require the exact HTTP loopback origin published by the Dev Compose stack."""

    try:
        parsed = urlsplit(base_url)
    except ValueError as error:
        raise ValueError("Dev smoke base URL is invalid") from error
    if (
        parsed.scheme != "http"
        or parsed.hostname != DEV_LOOPBACK_HOST
        or parsed.port != DEV_PUBLISHED_PORT
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Dev smoke base URL must be the loopback Dev origin")
    return DEFAULT_BASE_URL


class DevPostDeploySmoke:
    """Small stdlib HTTP client with a deliberately bounded cookie jar."""

    def __init__(self, base_url: str, open_url: _OpenUrl | None = None) -> None:
        self.base_url = normalize_base_url(base_url)
        self._cookie_jar = CookieJar()
        if open_url is None:
            opener = build_opener(ProxyHandler({}), _NoRedirectHandler())
            self._open_url = cast(_OpenUrl, opener.open)
        else:
            self._open_url = open_url

    def run(self) -> None:
        self._request(ROOT_PATH, "GET", "landing page")
        client_config = self._json_request(
            CLIENT_CONFIG_PATH,
            "GET",
            "client configuration",
        )
        api_prefix = self._validated_api_prefix(client_config)
        session = self._json_request(
            f"{api_prefix}{AUTH_GUEST_PATH_SUFFIX}",
            "POST",
            "guest authentication",
        )
        catalogue_path = f"{api_prefix}{CATALOGUE_PATH_SUFFIX}"
        self._validate_guest_session(session, catalogue_path)
        catalogue = self._json_request(
            catalogue_path,
            "GET",
            "guest catalogue",
        )
        self._validate_guest_catalogue(catalogue)

    @staticmethod
    def _validated_api_prefix(payload: object) -> str:
        if not isinstance(payload, dict):
            raise SmokeCheckError("client configuration response was malformed")
        api_prefix = payload.get("api_prefix")
        if (
            not isinstance(api_prefix, str)
            or API_PREFIX_PATTERN.fullmatch(api_prefix) is None
            or any(
                segment in NONCANONICAL_API_PREFIX_SEGMENTS
                for segment in api_prefix.split("/")[1:]
            )
            or any(
                api_prefix == root or api_prefix.startswith(f"{root}/")
                for root in RESERVED_API_PREFIX_ROOTS
            )
        ):
            raise SmokeCheckError("client configuration API prefix was malformed")
        return api_prefix

    def _request(self, path: str, method: str, label: str) -> _HttpResponse:
        if not path.startswith("/") or path.startswith("//"):
            raise SmokeCheckError("Dev smoke request path is invalid")
        request = Request(
            f"{self.base_url}{path}",
            data=b"" if method == "POST" else None,
            headers={
                "Accept": "application/json, text/html;q=0.9",
                "Origin": self.base_url,
                "Sec-Fetch-Site": "same-origin",
            },
            method=method,
        )
        self._cookie_jar.add_cookie_header(request)
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            csrf_token = self._request_cookie_value(request, CSRF_COOKIE_NAME)
            if csrf_token is None:
                raise SmokeCheckError("landing page did not issue a usable CSRF cookie")
            request.add_unredirected_header(CSRF_HEADER_NAME, csrf_token)
        try:
            with self._open_url(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                status = int(response.status)
                body = response.read(MAX_RESPONSE_BYTES + 1)
                self._cookie_jar.extract_cookies(
                    cast(HTTPResponse, response),
                    request,
                )
        except HTTPError as error:
            raise SmokeCheckError(f"{label} returned HTTP {error.code}") from None
        except SmokeCheckError:
            raise
        except (
            CookieError,
            HTTPException,
            OSError,
            TimeoutError,
            URLError,
            ValueError,
        ):
            raise SmokeCheckError(f"{label} was unavailable") from None
        if len(body) > MAX_RESPONSE_BYTES:
            raise SmokeCheckError(f"{label} response was too large")
        if not 200 <= status < 300:
            raise SmokeCheckError(f"{label} returned HTTP {status}")
        return _HttpResponse(status, body)

    @staticmethod
    def _request_cookie_value(request: Request, name: str) -> str | None:
        raw_header = request.get_header("Cookie")
        if raw_header is None:
            return None
        parsed = SimpleCookie()
        try:
            parsed.load(raw_header)
        except CookieError as error:
            raise SmokeCheckError("Dev smoke cookie state was invalid") from error
        morsel = parsed.get(name)
        return morsel.value if morsel is not None and morsel.value else None

    def _applicable_cookie_value(self, path: str, name: str) -> str | None:
        probe = Request(f"{self.base_url}{path}", method="GET")
        self._cookie_jar.add_cookie_header(probe)
        return self._request_cookie_value(probe, name)

    def _json_request(self, path: str, method: str, label: str) -> object:
        response = self._request(path, method, label)
        try:
            return json.loads(response.body.decode("utf-8"))
        except (RecursionError, UnicodeDecodeError, json.JSONDecodeError):
            raise SmokeCheckError(f"{label} returned malformed JSON") from None

    def _validate_guest_session(self, payload: object, catalogue_path: str) -> None:
        if self._applicable_cookie_value(catalogue_path, SESSION_COOKIE_NAME) is None:
            raise SmokeCheckError("guest authentication did not issue a usable session cookie")
        if not isinstance(payload, dict):
            raise SmokeCheckError("guest authentication response was malformed")
        if payload.get("principal_kind") != EXPECTED_PRINCIPAL_KIND:
            raise SmokeCheckError("guest authentication did not create a guest session")
        if payload.get("corpus_scope_revision") != EXPECTED_SCOPE_REVISION:
            raise SmokeCheckError("guest authentication scope is not the approved Dev scope")

    def _validate_guest_catalogue(self, payload: object) -> None:
        if not isinstance(payload, dict):
            raise SmokeCheckError("guest catalogue response was malformed")
        self._require_exact_fields(payload, CATALOGUE_FIELDS, "guest catalogue")
        schema_version = payload.get("schema_version")
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version != EXPECTED_CATALOGUE_SCHEMA_VERSION
        ):
            raise SmokeCheckError("guest catalogue schema is not the approved Dev schema")
        if payload.get("corpus_scope_revision") != EXPECTED_SCOPE_REVISION:
            raise SmokeCheckError("guest catalogue scope is not the approved Dev scope")
        series = payload.get("series")
        if not isinstance(series, list) or len(series) != 1:
            raise SmokeCheckError("guest catalogue entitlement contains unexpected series")
        item = series[0]
        if (
            not isinstance(item, dict)
            or item.get("series_id") != EXPECTED_SERIES_ID
            or item.get("series_name") != EXPECTED_SERIES_NAME
        ):
            raise SmokeCheckError("guest catalogue entitlement contains an unexpected series")
        self._require_exact_fields(item, SERIES_FIELDS, "guest catalogue series")
        seasons = item.get("seasons")
        if not isinstance(seasons, list) or len(seasons) != len(EXPECTED_SEASON_NUMBERS):
            raise SmokeCheckError("guest catalogue entitlement contains unexpected seasons")
        season_numbers: list[int] = []
        episode_ids: set[str] = set()
        for season in seasons:
            if not isinstance(season, dict):
                raise SmokeCheckError("guest catalogue entitlement contains malformed seasons")
            self._require_exact_fields(season, SEASON_FIELDS, "guest catalogue season")
            if not self._is_canonical_uuid(season.get("season_id")):
                raise SmokeCheckError("guest catalogue entitlement contains malformed seasons")
            number = season.get("season_number")
            if isinstance(number, bool) or not isinstance(number, int):
                raise SmokeCheckError("guest catalogue entitlement contains malformed seasons")
            season_numbers.append(number)
            episodes = season.get("episodes")
            if not isinstance(episodes, list):
                raise SmokeCheckError("guest catalogue entitlement contains malformed episodes")
            episode_numbers: list[int] = []
            for episode in episodes:
                if not isinstance(episode, dict):
                    raise SmokeCheckError("guest catalogue entitlement contains malformed episodes")
                self._require_exact_fields(episode, EPISODE_FIELDS, "guest catalogue episode")
                episode_id = episode.get("episode_id")
                episode_number = episode.get("episode_number")
                episode_title = episode.get("episode_title")
                if (
                    not isinstance(episode_id, str)
                    or not self._is_canonical_uuid(episode_id)
                    or episode_id in episode_ids
                    or isinstance(episode_number, bool)
                    or not isinstance(episode_number, int)
                    or (episode_title is not None and not isinstance(episode_title, str))
                ):
                    raise SmokeCheckError("guest catalogue entitlement contains malformed episodes")
                episode_ids.add(episode_id)
                episode_numbers.append(episode_number)
                self._validate_credits(episode.get("guest_cast"), "episode guest cast")
            if set(episode_numbers) != EXPECTED_EPISODE_NUMBERS or len(
                episode_numbers
            ) != len(EXPECTED_EPISODE_NUMBERS):
                raise SmokeCheckError("guest catalogue entitlement contains incomplete episodes")
        if set(season_numbers) != EXPECTED_SEASON_NUMBERS:
            raise SmokeCheckError("guest catalogue entitlement is not exactly Modern Family seasons 1 and 2")
        self._validate_poster(item.get("poster"))
        self._validate_credits(item.get("regular_cast"), "series regular cast")
        self._validate_metadata_source(item.get("metadata_source"))

    @staticmethod
    def _require_exact_fields(
        payload: dict[object, object],
        expected: frozenset[str],
        label: str,
    ) -> None:
        if set(payload) != expected:
            raise SmokeCheckError(f"{label} response shape was malformed")

    @staticmethod
    def _is_canonical_uuid(value: object) -> bool:
        if not isinstance(value, str):
            return False
        try:
            return str(UUID(value)) == value
        except ValueError:
            return False

    def _validate_credits(self, payload: object, label: str) -> None:
        if not isinstance(payload, list):
            raise SmokeCheckError(f"guest catalogue {label} was malformed")
        for credit in payload:
            if not isinstance(credit, dict):
                raise SmokeCheckError(f"guest catalogue {label} was malformed")
            self._require_exact_fields(credit, CREDIT_FIELDS, f"guest catalogue {label}")
            if not all(
                isinstance(credit.get(field), str)
                for field in ("name", "character_name", "credit_kind", "canonical_url")
            ) or (
                credit.get("character_canonical_url") is not None
                and not isinstance(credit.get("character_canonical_url"), str)
            ):
                raise SmokeCheckError(f"guest catalogue {label} was malformed")

    def _validate_poster(self, payload: object) -> None:
        if payload is None:
            return
        if not isinstance(payload, dict):
            raise SmokeCheckError("guest catalogue poster was malformed")
        self._require_exact_fields(payload, POSTER_FIELDS, "guest catalogue poster")
        if not all(
            isinstance(payload.get(field), str)
            for field in ("url", "alt", "attribution", "license_name", "license_url")
        ) or any(
            value is not None and (isinstance(value, bool) or not isinstance(value, int))
            for value in (payload.get("width"), payload.get("height"))
        ):
            raise SmokeCheckError("guest catalogue poster was malformed")

    def _validate_metadata_source(self, payload: object) -> None:
        if payload is None:
            return
        if not isinstance(payload, dict):
            raise SmokeCheckError("guest catalogue metadata source was malformed")
        self._require_exact_fields(
            payload,
            METADATA_SOURCE_FIELDS,
            "guest catalogue metadata source",
        )
        if not all(isinstance(value, str) for value in payload.values()):
            raise SmokeCheckError("guest catalogue metadata source was malformed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args()
    try:
        DevPostDeploySmoke(args.base_url).run()
    except (SmokeCheckError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Dev post-deploy smoke passed: guest entitlement is Modern Family seasons 1 and 2.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
