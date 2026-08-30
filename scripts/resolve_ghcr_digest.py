"""Resolve an immutable Cinegraph GHCR release tag to its manifest digest."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Final, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from scripts.container_image_contract import (
    EXPECTED_IMAGE_NAME,
    REGISTRY_ACCEPT_HEADER,
    is_image_digest,
    release_tag,
)

REGISTRY_REPOSITORY: Final = EXPECTED_IMAGE_NAME.removeprefix("ghcr.io/")
TOKEN_URL: Final = f"https://ghcr.io/token?service=ghcr.io&scope=repository:{REGISTRY_REPOSITORY}:pull"
MANIFEST_URL_TEMPLATE: Final = f"https://ghcr.io/v2/{REGISTRY_REPOSITORY}/manifests/{{tag}}"
HTTP_TIMEOUT_SECONDS: Final = 30.0


class ResponseLike(Protocol):
    status: int
    headers: Any

    def read(self) -> bytes: ...

    def __enter__(self) -> "ResponseLike": ...

    def __exit__(self, *args: object) -> None: ...


Urlopen = Callable[..., AbstractContextManager[ResponseLike]]


class RegistryResolutionError(RuntimeError):
    """Raised when the release cannot be resolved safely."""


def _request_json(request: Request, opener: Urlopen) -> dict[str, Any]:
    try:
        with opener(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise RegistryResolutionError("GHCR token endpoint returned an unexpected status")
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, TimeoutError, UnicodeError, json.JSONDecodeError) as error:
        raise RegistryResolutionError("GHCR token request failed") from error
    if not isinstance(payload, dict):
        raise RegistryResolutionError("GHCR token response was not an object")
    return payload


def _resolve_pull_token(actor: str, github_token: str, opener: Urlopen) -> str:
    credentials = base64.b64encode(f"{actor}:{github_token}".encode("utf-8")).decode("ascii")
    request = Request(TOKEN_URL, headers={"Authorization": f"Basic {credentials}"})
    payload = _request_json(request, opener)
    token = payload.get("token") or payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise RegistryResolutionError("GHCR token response did not contain a pull token")
    return token


def resolve_digest(
    release_sha: str,
    *,
    actor: str,
    github_token: str,
    opener: Urlopen = urlopen,
) -> str:
    """Resolve a release tag using an injectable urllib transport."""

    tag = release_tag(release_sha)
    if not actor or not github_token:
        raise RegistryResolutionError("GHCR credentials are required")
    pull_token = _resolve_pull_token(actor, github_token, opener)
    request = Request(
        MANIFEST_URL_TEMPLATE.format(tag=tag),
        headers={"Authorization": f"Bearer {pull_token}", "Accept": REGISTRY_ACCEPT_HEADER},
    )
    try:
        with opener(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise RegistryResolutionError("GHCR manifest endpoint returned an unexpected status")
            response.read()
            digest = response.headers.get("Docker-Content-Digest")
    except (HTTPError, URLError, OSError, TimeoutError) as error:
        raise RegistryResolutionError("GHCR manifest request failed") from error
    if not isinstance(digest, str) or not is_image_digest(digest):
        raise RegistryResolutionError("GHCR manifest did not return a valid immutable digest")
    return digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-sha", required=True)
    args = parser.parse_args(argv)
    try:
        digest = resolve_digest(
            args.release_sha,
            actor=os.environ.get("GITHUB_ACTOR", ""),
            github_token=os.environ.get("GITHUB_TOKEN", ""),
        )
    except (RegistryResolutionError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
