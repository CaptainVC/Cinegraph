from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.error import URLError

import pytest
from scripts.resolve_ghcr_digest import RegistryResolutionError, resolve_digest


class _Response:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self._body = body
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _Opener:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = iter(responses)
        self.requests: list[Any] = []

    def __call__(self, request: Any, *, timeout: float) -> Iterator[_Response]:
        self.requests.append((request, timeout))
        response = next(self.responses)

        @contextmanager
        def response_context() -> Iterator[_Response]:
            yield response

        return response_context()


def test_resolves_exact_lowercase_manifest_digest_with_bounded_transport() -> None:
    opener = _Opener(
        [
            _Response(200, b'{"token":"pull-token"}'),
            _Response(200, b"", {"Docker-Content-Digest": "sha256:" + "a" * 64}),
        ]
    )

    digest = resolve_digest("b" * 40, actor="cinegraph", github_token="secret", opener=opener)

    assert digest == "sha256:" + "a" * 64
    assert opener.requests[0][1] == 30.0
    assert opener.requests[1][1] == 30.0
    assert opener.requests[1][0].headers["Accept"]
    assert opener.requests[1][0].headers["Authorization"] == "Bearer pull-token"
    assert "secret" not in opener.requests[0][0].headers["Authorization"]


@pytest.mark.parametrize(
    ("token_response", "manifest_response"),
    [
        (_Response(401, b"unauthorized"), None),
        (_Response(200, b"[]"), None),
        (_Response(200, b'{"token":"pull-token"}'), _Response(404, b"missing")),
        (_Response(200, b'{"token":"pull-token"}'), _Response(500, b"error")),
        (
            _Response(200, b'{"token":"pull-token"}'),
            _Response(200, b"manifest", {"Docker-Content-Digest": "sha256:" + "A" * 64}),
        ),
        (_Response(200, b'{"token":"pull-token"}'), _Response(200, b"manifest")),
    ],
)
def test_rejects_invalid_registry_or_digest_responses(
    token_response: _Response,
    manifest_response: _Response | None,
) -> None:
    responses = [token_response] if manifest_response is None else [token_response, manifest_response]
    opener = _Opener(responses)

    with pytest.raises(RegistryResolutionError):
        resolve_digest("b" * 40, actor="cinegraph", github_token="secret", opener=opener)


@pytest.mark.parametrize("release_sha", ["A" * 40, "a" * 39, "not-a-sha"])
def test_rejects_noncanonical_release_sha(release_sha: str) -> None:
    with pytest.raises(ValueError):
        resolve_digest(release_sha, actor="cinegraph", github_token="secret", opener=_Opener([]))


def test_requires_registry_credentials() -> None:
    with pytest.raises(RegistryResolutionError):
        resolve_digest("b" * 40, actor="", github_token="", opener=_Opener([]))


def test_network_transport_failure_is_fail_closed() -> None:
    def failing_opener(request: Any, *, timeout: float) -> Any:
        raise URLError("secret-token-must-not-be-echoed")

    with pytest.raises(RegistryResolutionError, match="token request failed"):
        resolve_digest("b" * 40, actor="cinegraph", github_token="secret-token", opener=failing_opener)


def test_empty_manifest_body_does_not_echo_registry_content(capsys: pytest.CaptureFixture[str]) -> None:
    opener = _Opener(
        [
            _Response(200, b'{"token":"secret-token"}'),
            _Response(200, b"", {"Docker-Content-Digest": "sha256:" + "c" * 64}),
        ]
    )

    assert resolve_digest("d" * 40, actor="cinegraph", github_token="secret-token", opener=opener) == (
        "sha256:" + "c" * 64
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
