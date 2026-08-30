"""Shared, stdlib-only image identity contract for release tooling."""

from __future__ import annotations

import re
from typing import Final

EXPECTED_IMAGE_NAME: Final = "ghcr.io/captainvc/cinegraph"
IMAGE_DIGEST_PATTERN: Final = r"^sha256:[0-9a-f]{64}$"
RELEASE_SHA_PATTERN: Final = r"^[0-9a-f]{40}$"
REGISTRY_ACCEPT_HEADER: Final = (
    "application/vnd.oci.image.index.v1+json, "
    "application/vnd.oci.image.manifest.v1+json, "
    "application/vnd.docker.distribution.manifest.list.v2+json, "
    "application/vnd.docker.distribution.manifest.v2+json"
)


def is_release_sha(value: str) -> bool:
    return re.fullmatch(RELEASE_SHA_PATTERN, value) is not None


def is_image_digest(value: str) -> bool:
    return re.fullmatch(IMAGE_DIGEST_PATTERN, value) is not None


def release_tag(release_sha: str) -> str:
    if not is_release_sha(release_sha):
        raise ValueError("release SHA must be a lowercase 40-character hexadecimal string")
    return f"sha-{release_sha}"
