import json
from pathlib import Path

import pytest
from scripts.review_series_metadata_snapshot import (
    _magic_matches,
    download_poster,
    review_and_publish,
)
from tests.unit.adapters.catalogue.test_series_metadata_snapshot_loader import (
    manifest_file,
    write_snapshot,
)

from cinegraph.adapters.catalogue.json_catalogue_manifest_loader import JsonCatalogueManifestLoader
from cinegraph.adapters.catalogue.series_metadata_snapshot_loader import (
    parse_series_metadata_snapshot,
)
from cinegraph.domain.enums.enum import SourceReviewStatus

JPEG = b"\xff\xd8\xff" + b"synthetic-jpeg"
PNG = b"\x89PNG\r\n\x1a\nsynthetic-png"
WEBP = b"RIFF\x00\x00\x00\x00WEBPsynthetic-webp"


class _Poster:
    original_url = "https://static.tvmaze.com/uploads/images/original_untouched/1/2.jpg"
    medium_url = "https://static.tvmaze.com/uploads/images/medium_portrait/1/2.jpg"


class _Response:
    def __init__(self, content: bytes, content_type: str, status_code: int = 200) -> None:
        self.status_code = status_code
        self.headers = {"content-type": content_type, "content-length": str(len(content))}
        self._content = content

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def iter_bytes(self):
        yield self._content[:4]
        yield self._content[4:]


class _Client:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.urls: list[str] = []

    def stream(self, *, url: str, method: str, follow_redirects: bool):
        assert method == "GET"
        assert follow_redirects is False
        self.urls.append(url)
        return self.responses.pop(0)


@pytest.mark.parametrize(
    ("mime", "content"),
    [("image/jpeg", JPEG), ("image/png", PNG), ("image/webp", WEBP)],
)
def test_magic_bytes_are_typed(mime: str, content: bytes) -> None:
    assert _magic_matches(mime, content)


def test_download_uses_original_then_medium_fallback() -> None:
    client = _Client(
        [
            _Response(b"not-an-image", "text/plain"),
            _Response(PNG, "image/png; charset=binary"),
        ]
    )
    assert download_poster(_Poster(), client) == PNG
    assert len(client.urls) == 2


@pytest.mark.parametrize(
    "response",
    [
        _Response(b"redirect", "image/png", 302),
        _Response(PNG + b"x", "image/jpeg"),
        _Response(b"x" * 32, "image/png"),
    ],
)
def test_download_rejects_redirect_mime_and_oversize(response: _Response) -> None:
    client = _Client([response, response])
    with pytest.raises(RuntimeError):
        download_poster(_Poster(), client, max_bytes=16)


def test_review_publishes_atomically_and_is_idempotent(tmp_path: Path) -> None:
    manifest = tmp_path / "knowledge" / "catalogue.json"
    manifest.parent.mkdir(parents=True)
    manifest_file(manifest)
    pending = manifest.parent / "series-metadata" / "pending" / "modern-family.json"
    write_snapshot(pending)
    approved = manifest.parent / "series-metadata" / "approved" / "modern-family.json"
    artwork = manifest.parent / "series-metadata" / "artwork" / (
        "00000000-0000-0000-0000-000000000011.poster"
    )
    # The synthetic fixture has no poster, so no network is needed and no artwork is emitted.
    client = _Client([])
    assert review_and_publish(
        manifest_path=manifest,
        input_path=pending,
        output_path=approved,
        artwork_path=artwork,
        client=client,
    )
    assert approved.is_file()
    version, _ = parse_series_metadata_snapshot(
        approved, JsonCatalogueManifestLoader().load(manifest).manifest
    )
    assert version.review_status is SourceReviewStatus.AUTOMATED_REVIEWED
    assert review_and_publish(
        manifest_path=manifest,
        input_path=pending,
        output_path=approved,
        artwork_path=artwork,
        client=_Client([]),
    ) is False


def test_review_downloads_original_then_medium_and_writes_series_artwork(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "knowledge" / "catalogue.json"
    manifest.parent.mkdir(parents=True)
    manifest_file(manifest)
    pending = manifest.parent / "series-metadata" / "pending" / "modern-family.json"
    write_snapshot(pending, with_poster=True)
    approved = manifest.parent / "series-metadata" / "approved" / "modern-family.json"
    artwork = manifest.parent / "series-metadata" / "artwork" / (
        "00000000-0000-0000-0000-000000000011.poster"
    )
    client = _Client(
        [_Response(b"not-an-image", "text/plain"), _Response(PNG, "image/png")]
    )
    assert review_and_publish(
        manifest_path=manifest,
        input_path=pending,
        output_path=approved,
        artwork_path=artwork,
        client=client,
    )
    assert artwork.read_bytes() == PNG
    assert client.urls == [
        "https://static.tvmaze.com/uploads/images/original_untouched/1/2.jpg",
        "https://static.tvmaze.com/uploads/images/medium_portrait/1/2.jpg",
    ]


def test_same_hash_approved_output_repairs_missing_artwork_without_rewriting_json(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "knowledge" / "catalogue.json"
    manifest.parent.mkdir(parents=True)
    manifest_file(manifest)
    pending = manifest.parent / "series-metadata" / "pending" / "modern-family.json"
    write_snapshot(pending, with_poster=True)
    approved = manifest.parent / "series-metadata" / "approved" / "modern-family.json"
    artwork = manifest.parent / "series-metadata" / "artwork" / (
        "00000000-0000-0000-0000-000000000011.poster"
    )
    first_client = _Client([_Response(PNG, "image/png")])
    review_and_publish(
        manifest_path=manifest,
        input_path=pending,
        output_path=approved,
        artwork_path=artwork,
        client=first_client,
    )
    original_json = approved.read_bytes()
    artwork.unlink()
    second_client = _Client([_Response(PNG, "image/png")])
    assert review_and_publish(
        manifest_path=manifest,
        input_path=pending,
        output_path=approved,
        artwork_path=artwork,
        client=second_client,
    ) is False
    assert artwork.read_bytes() == PNG
    assert approved.read_bytes() == original_json


def test_same_hash_invalid_existing_artwork_is_protected(tmp_path: Path) -> None:
    manifest = tmp_path / "knowledge" / "catalogue.json"
    manifest.parent.mkdir(parents=True)
    manifest_file(manifest)
    pending = manifest.parent / "series-metadata" / "pending" / "modern-family.json"
    write_snapshot(pending, with_poster=True)
    approved = manifest.parent / "series-metadata" / "approved" / "modern-family.json"
    artwork = manifest.parent / "series-metadata" / "artwork" / (
        "00000000-0000-0000-0000-000000000011.poster"
    )
    review_and_publish(
        manifest_path=manifest,
        input_path=pending,
        output_path=approved,
        artwork_path=artwork,
        client=_Client([_Response(PNG, "image/png")]),
    )
    artwork.write_bytes(b"corrupt")
    with pytest.raises(FileExistsError):
        review_and_publish(
            manifest_path=manifest,
            input_path=pending,
            output_path=approved,
            artwork_path=artwork,
            client=_Client([_Response(PNG, "image/png")]),
        )


def test_existing_different_output_is_protected(tmp_path: Path) -> None:
    manifest = tmp_path / "knowledge" / "catalogue.json"
    manifest.parent.mkdir(parents=True)
    manifest_file(manifest)
    pending = manifest.parent / "series-metadata" / "pending" / "modern-family.json"
    write_snapshot(pending)
    approved = manifest.parent / "series-metadata" / "approved" / "modern-family.json"
    approved.parent.mkdir(parents=True)
    approved.write_text(json.dumps({"tampered": True}), encoding="utf-8")
    with pytest.raises(FileExistsError):
        review_and_publish(
            manifest_path=manifest,
            input_path=pending,
            output_path=approved,
            artwork_path=manifest.parent / "series-metadata" / "artwork" / "x.poster",
            client=_Client([]),
        )
