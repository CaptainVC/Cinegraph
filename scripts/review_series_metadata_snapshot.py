"""Deterministically validate, fetch artwork for, and publish a TVmaze snapshot."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable
from uuid import UUID

import httpx

from cinegraph.adapters.catalogue.json_catalogue_manifest_loader import (
    JsonCatalogueManifestLoader,
)
from cinegraph.adapters.catalogue.json_series_metadata_snapshot_loader import (
    parse_series_metadata_snapshot,
)
from cinegraph.adapters.source.tvmaze_constants import (
    TVMAZE_ALLOWED_CONTENT_HOSTS,
    TVMAZE_USER_AGENT,
)
from cinegraph.application.serialization.series_metadata_snapshot_serializer import (
    export_ingestion_result,
)
from cinegraph.config.series_metadata import (
    SERIES_METADATA_APPROVED_DIRECTORY,
    SERIES_METADATA_ARTWORK_DIRECTORY,
    SERIES_METADATA_PENDING_DIRECTORY,
    SERIES_METADATA_POSTER_CONNECT_TIMEOUT_SECONDS,
    SERIES_METADATA_POSTER_CONTENT_TYPES,
    SERIES_METADATA_POSTER_MAX_BYTES,
    SERIES_METADATA_POSTER_TIMEOUT_SECONDS,
    SERIES_METADATA_REVIEWER_ID,
)
from cinegraph.domain.enums.enum import SourceKind, SourceReviewStatus
from cinegraph.domain.models.source.source_document import SourceDocument
from cinegraph.domain.models.source.source_version import SourceVersion
from cinegraph.ingestion.series_metadata.ingest_series_metadata import (
    IngestSeriesMetadataResult,
)


def parse_uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"Expected a UUID, received: {value}") from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deterministically review and publish a pending TVmaze metadata snapshot."
    )
    parser.add_argument("--manifest", type=Path, default=Path("knowledge/catalogue.json"))
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--artwork-output", type=Path)
    parser.add_argument("--force", action="store_true")
    return parser


def _contained(root: Path, candidate: Path, detail: str) -> Path:
    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve()
    try:
        candidate_resolved.relative_to(root_resolved)
    except ValueError as error:
        raise ValueError(detail) from error
    return candidate_resolved


def _atomic_write(path: Path, content: bytes, *, force: bool) -> bool:
    if path.exists():
        if path.read_bytes() == content:
            return False
        if not force:
            raise FileExistsError(
                f"Output exists with different content: {path}; pass --force to replace it."
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    return True


def _magic_matches(content_type: str, content: bytes) -> bool:
    if content_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    return content.startswith(b"RIFF") and content[8:12] == b"WEBP"


def _trusted_image_url(value: str) -> None:
    parsed = httpx.URL(value)
    if (
        parsed.scheme != "https"
        or parsed.host not in TVMAZE_ALLOWED_CONTENT_HOSTS
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("Artwork URL is not an allowed HTTPS TVmaze URL.")


def download_poster(
    poster: object,
    client: httpx.Client,
    *,
    max_bytes: int = SERIES_METADATA_POSTER_MAX_BYTES,
) -> bytes:
    """Download original then medium artwork with bounded, typed responses."""
    original = getattr(poster, "original_url", None)
    medium = getattr(poster, "medium_url", None)
    candidates = tuple(item for item in (original, medium) if item)
    last_error: Exception | None = None
    for url in candidates:
        try:
            _trusted_image_url(url)
            with client.stream(url=url, method="GET", follow_redirects=False) as response:
                if response.status_code != 200:
                    raise ValueError("Artwork provider returned a non-200 response.")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if content_type not in SERIES_METADATA_POSTER_CONTENT_TYPES:
                    raise ValueError("Artwork response has an unsupported content type.")
                declared_length = response.headers.get("content-length")
                if declared_length is not None:
                    try:
                        declared_bytes = int(declared_length)
                        if declared_bytes < 0 or declared_bytes > max_bytes:
                            raise ValueError("Artwork response exceeds the configured byte limit.")
                    except ValueError as error:
                        if "exceeds" in str(error):
                            raise
                        raise ValueError("Artwork response has an invalid byte length.") from error
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("Artwork response exceeds the configured byte limit.")
                    chunks.append(chunk)
                content = b"".join(chunks)
                if not _magic_matches(content_type, content):
                    raise ValueError("Artwork response magic bytes do not match its MIME type.")
                return content
        except (httpx.HTTPError, ValueError, TypeError) as error:
            last_error = error
    raise RuntimeError("Unable to download a valid TVmaze poster.") from last_error


def _approved_version(version: SourceVersion, reviewed_at: datetime) -> SourceVersion:
    return SourceVersion(
        source_version_id=version.source_version_id,
        source_document_id=version.source_document_id,
        content_hash=version.content_hash,
        rights_status=version.rights_status,
        acquisition_method=version.acquisition_method,
        review_status=SourceReviewStatus.AUTOMATED_REVIEWED,
        status=version.status,
        acquired_at=version.acquired_at,
        parent_source_version_id=version.parent_source_version_id,
        reviewed_by=SERIES_METADATA_REVIEWER_ID,
        reviewed_at=reviewed_at,
    )


def review_and_publish(
    *,
    manifest_path: Path,
    input_path: Path,
    output_path: Path,
    artwork_path: Path,
    client: httpx.Client,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    force: bool = False,
) -> bool:
    loaded = JsonCatalogueManifestLoader().load(manifest_path)
    version, snapshot = parse_series_metadata_snapshot(
        input_path, loaded.manifest, approved_only=False
    )
    if version.review_status is not SourceReviewStatus.PENDING:
        raise ValueError("Only pending metadata snapshots can be deterministically reviewed.")
    output_bytes_existing = output_path.read_bytes() if output_path.is_file() else None
    same_hash_existing = False
    if output_bytes_existing is not None:
        try:
            existing_version, _ = parse_series_metadata_snapshot(
                output_path, loaded.manifest, approved_only=True
            )
            if existing_version.content_hash == version.content_hash:
                same_hash_existing = True
                if snapshot.poster is None:
                    if artwork_path.exists() and not force:
                        raise FileExistsError(
                            f"Artwork exists but this snapshot has no poster: {artwork_path}; "
                            "pass --force."
                        )
                    if artwork_path.exists():
                        artwork_path.unlink()
                    return False
                if artwork_path.is_file():
                    existing_artwork = artwork_path.read_bytes()
                    if len(existing_artwork) <= SERIES_METADATA_POSTER_MAX_BYTES and any(
                        _magic_matches(mime, existing_artwork)
                        for mime in SERIES_METADATA_POSTER_CONTENT_TYPES
                    ):
                        return False
                if artwork_path.exists() and not force:
                    raise FileExistsError(
                        f"Artwork exists with invalid content: {artwork_path}; pass --force."
                    )
            elif not force:
                raise FileExistsError(
                    f"Output exists with different content: {output_path}; pass --force."
                )
        except (OSError, ValueError):
            if not force:
                raise FileExistsError(
                    f"Output exists with invalid or different content: {output_path}; "
                    "pass --force to replace it."
                )
    artwork = download_poster(snapshot.poster, client) if snapshot.poster is not None else None
    if artwork is not None:
        _atomic_write(artwork_path, artwork, force=force)
    elif artwork_path.exists() and not force:
        raise FileExistsError(
            f"Artwork exists but this snapshot has no poster: {artwork_path}; pass --force."
        )
    elif artwork_path.exists():
        artwork_path.unlink()
    if same_hash_existing:
        return False
    reviewed_at = clock()
    approved_version = _approved_version(version, reviewed_at)
    source_document = SourceDocument(
        version.source_document_id,
        f"TVmaze metadata for {snapshot.title}",
        SourceKind.METADATA,
        "tvmaze",
    )
    result = IngestSeriesMetadataResult(approved_version, snapshot, False)
    payload = export_ingestion_result(source_document, result)
    output = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )
    _atomic_write(output_path, output, force=force)
    return True


def main() -> None:
    args = build_parser().parse_args()
    knowledge_root = args.manifest.parent
    input_path = _contained(
        knowledge_root,
        args.input,
        "Input must be inside the catalogue manifest's knowledge directory.",
    )
    pending_root = (knowledge_root / SERIES_METADATA_PENDING_DIRECTORY).resolve()
    if pending_root not in input_path.parents:
        raise ValueError("Input must be inside the pending series metadata directory.")
    output_path = _contained(
        knowledge_root,
        args.output
        or knowledge_root / SERIES_METADATA_APPROVED_DIRECTORY / input_path.name,
        "Output must be inside the catalogue manifest's knowledge directory.",
    )
    approved_root = (knowledge_root / SERIES_METADATA_APPROVED_DIRECTORY).resolve()
    if approved_root not in output_path.parents:
        raise ValueError("Output must be inside the approved series metadata directory.")
    artwork_root = (knowledge_root / SERIES_METADATA_ARTWORK_DIRECTORY).resolve()
    loaded = JsonCatalogueManifestLoader().load(args.manifest)
    _, pending_snapshot = parse_series_metadata_snapshot(
        input_path, loaded.manifest, approved_only=False
    )
    artwork_path = _contained(
        knowledge_root,
        args.artwork_output or artwork_root / f"{pending_snapshot.series_id}.poster",
        "Artwork must be inside the catalogue manifest's knowledge directory.",
    )
    if artwork_root not in artwork_path.parents:
        raise ValueError("Artwork must be inside the series metadata artwork directory.")
    if artwork_path != artwork_root / f"{pending_snapshot.series_id}.poster":
        raise ValueError("Artwork output must use the catalogue series ID filename.")
    timeout = httpx.Timeout(
        SERIES_METADATA_POSTER_TIMEOUT_SECONDS,
        connect=SERIES_METADATA_POSTER_CONNECT_TIMEOUT_SECONDS,
    )
    with httpx.Client(timeout=timeout, headers={"User-Agent": TVMAZE_USER_AGENT}) as client:
        review_and_publish(
            manifest_path=args.manifest,
            input_path=input_path,
            output_path=output_path,
            artwork_path=artwork_path,
            client=client,
            force=args.force,
        )


if __name__ == "__main__":
    main()
