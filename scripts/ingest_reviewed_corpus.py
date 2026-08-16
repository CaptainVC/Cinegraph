from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from qdrant_client import QdrantClient

from cinegraph.adapters.catalogue import (
    JsonCatalogueManifestLoader,
    ReviewedSubtitleLedgerLoader,
)
from cinegraph.adapters.date_time.system_clock import SystemClock
from cinegraph.adapters.ingestion.finalized_srt_canonicalizer import (
    FinalizedSrtCanonicalizer,
)
from cinegraph.adapters.qdrant.qdrant_collection_provisioner import (
    QdrantTranscriptCollectionProvisioner,
)
from cinegraph.adapters.qdrant.qdrant_transcript_index_writer import (
    QdrantTranscriptIndexWriter,
)
from cinegraph.adapters.repository.in_memory.in_memory_transcript_ingestion_repository import (
    InMemoryTranscriptIngestionRepository,
)
from cinegraph.adapters.retrieval.fastembed_vector_encoder import (
    FastEmbedVectorEncoder,
)
from cinegraph.adapters.source.local_subtitle_text_reader import (
    LocalSubtitleTextReader,
)
from cinegraph.application.models.ingest_reviewed_corpus import (
    IngestReviewedCorpusCommand,
)
from cinegraph.application.service.index_transcript_segments_service import (
    IndexTranscriptSegmentsService,
)
from cinegraph.application.service.ingest_reviewed_corpus_service import (
    IngestReviewedCorpusService,
)
from cinegraph.application.service.ingest_reviewed_subtitle_service import (
    IngestReviewedSubtitleService,
)
from cinegraph.config import DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA
from cinegraph.config.qdrant import (
    QDRANT_API_KEY_ENVIRONMENT_VARIABLE,
    QDRANT_DEFAULT_URL,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or index ledger-approved private subtitle files."
    )
    parser.add_argument("--catalogue-manifest", required=True, type=Path)
    parser.add_argument("--review-ledger", required=True, type=Path)
    parser.add_argument("--reviewed-directory", required=True, type=Path)
    parser.add_argument("--qdrant-url", default=QDRANT_DEFAULT_URL)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Provision Qdrant and upsert transcript vectors; default is validation only.",
    )
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    loaded_manifest = JsonCatalogueManifestLoader().load(arguments.catalogue_manifest)
    batch = ReviewedSubtitleLedgerLoader().load(
        loaded_manifest.manifest,
        arguments.review_ledger,
        arguments.reviewed_directory,
    )
    summary: dict[str, object] = {
        "catalogue_sha256": loaded_manifest.content_sha256,
        "episode_count": len(batch.items),
        "mode": "apply" if arguments.apply else "validate",
    }
    if not arguments.apply:
        print(json.dumps(summary, sort_keys=True))
        return

    client = QdrantClient(
        url=arguments.qdrant_url,
        api_key=os.getenv(QDRANT_API_KEY_ENVIRONMENT_VARIABLE),
    )
    provisioning = QdrantTranscriptCollectionProvisioner(client).provision()
    encoder = FastEmbedVectorEncoder.from_default_models()
    ingestion = IngestReviewedSubtitleService(
        InMemoryTranscriptIngestionRepository(),
        LocalSubtitleTextReader(),
        FinalizedSrtCanonicalizer(),
        SystemClock(),
    )
    indexing = IndexTranscriptSegmentsService(
        encoder,
        QdrantTranscriptIndexWriter(
            client,
            DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA.collection_name,
        ),
    )
    result = IngestReviewedCorpusService(ingestion, indexing).execute(
        IngestReviewedCorpusCommand(batch=batch)
    )
    summary.update(
        {
            "collection_created": provisioning.collection_created,
            "payload_indexes_created": list(
                provisioning.payload_indexes_created
            ),
            "indexed_segment_count": result.indexed_segment_count,
            "source_version_ids": [
                str(outcome.source_version_id) for outcome in result.outcomes
            ],
        }
    )
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
