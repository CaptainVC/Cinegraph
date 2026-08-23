from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import UUID

import httpx

from cinegraph.adapters.catalogue.json_catalogue_manifest_loader import (
    JsonCatalogueManifestLoader,
)
from cinegraph.adapters.date_time.system_clock import SystemClock
from cinegraph.adapters.repository.in_memory.in_memory_series_metadata_ingestion_repository import (
    InMemorySeriesMetadataIngestionRepository,
)
from cinegraph.adapters.source.tvmaze_constants import (
    TVMAZE_CONNECT_TIMEOUT_SECONDS,
    TVMAZE_TIMEOUT_SECONDS,
    TVMAZE_USER_AGENT,
)
from cinegraph.adapters.source.tvmaze_series_metadata_provider import (
    TVMazeSeriesMetadataProvider,
)
from cinegraph.application.serialization.series_metadata_snapshot_serializer import (
    export_json,
)
from cinegraph.application.service.ingest_series_metadata_service import (
    IngestSeriesMetadataService,
)
from cinegraph.common.identifiers.generator import IdentifierGenerator
from cinegraph.domain.enums.enum import SourceKind
from cinegraph.domain.models.source.source_document import SourceDocument
from cinegraph.ingestion.series_metadata.ingest_series_metadata import (
    IngestSeriesMetadataCommand,
    IngestSeriesMetadataResult,
)


def parse_uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"Expected a UUID, received: {value}"
        ) from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Acquire a canonical TVmaze series metadata snapshot."
    )
    parser.add_argument(
        "--manifest", type=Path, default=Path("knowledge/catalogue.json")
    )
    parser.add_argument("--series-id", required=True, type=parse_uuid)
    parser.add_argument("--tvmaze-show-id", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    return parser


def _output_path(manifest_path: Path, requested_output: Path) -> Path:
    knowledge_root = manifest_path.parent.resolve()
    output_path = requested_output.resolve()
    try:
        output_path.relative_to(knowledge_root)
    except ValueError as error:
        raise ValueError(
            "Output must be inside the catalogue manifest's knowledge directory."
        ) from error
    return output_path


def acquire(
    manifest_path: Path,
    series_id: UUID,
    tvmaze_show_id: int,
    client: httpx.Client,
) -> tuple[SourceDocument, IngestSeriesMetadataResult]:
    loaded = JsonCatalogueManifestLoader().load(manifest_path)
    series = next(
        (item for item in loaded.manifest.series if item.series_id == series_id), None
    )
    if series is None:
        raise ValueError(f"Series was not found in the catalogue manifest: {series_id}")
    episodes = tuple(
        ref for ref in loaded.manifest.episode_refs() if ref.series_id == series_id
    )
    source_document = SourceDocument(
        source_document_id=IdentifierGenerator.series_metadata_source_document_id(
            series_id, "tvmaze"
        ),
        title=f"TVmaze metadata for {series.series_name}",
        kind=SourceKind.METADATA,
        origin="tvmaze",
    )
    result = IngestSeriesMetadataService(
        TVMazeSeriesMetadataProvider(client=client, clock=SystemClock()),
        InMemorySeriesMetadataIngestionRepository(),
    ).execute(
        IngestSeriesMetadataCommand(
            source_document=source_document,
            series_id=series_id,
            provider_show_id=tvmaze_show_id,
            expected_title=series.series_name,
            episodes=episodes,
        )
    )
    return source_document, result


def write_snapshot(
    output_path: Path,
    source_document: SourceDocument,
    result: IngestSeriesMetadataResult,
    *,
    force: bool = False,
) -> bool:
    output = export_json(source_document, result)
    if output_path.exists():
        existing = output_path.read_text(encoding="utf-8")
        if existing == output:
            return False
        try:
            existing_hash = json.loads(existing)["source_version"]["content_hash"]
        except (json.JSONDecodeError, KeyError, TypeError):
            existing_hash = None
        if existing_hash == result.source_version.content_hash:
            return False
        if not force:
            raise FileExistsError(
                "Output exists with different content; pass --force to replace it."
            )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output, encoding="utf-8")
    return True


def main() -> None:
    args = build_parser().parse_args()
    output_path = _output_path(args.manifest, args.output)
    timeout = httpx.Timeout(
        TVMAZE_TIMEOUT_SECONDS, connect=TVMAZE_CONNECT_TIMEOUT_SECONDS
    )
    with httpx.Client(
        timeout=timeout, headers={"User-Agent": TVMAZE_USER_AGENT}
    ) as client:
        source_document, result = acquire(
            args.manifest, args.series_id, args.tvmaze_show_id, client
        )
    write_snapshot(output_path, source_document, result, force=args.force)


if __name__ == "__main__":
    main()
