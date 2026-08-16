from __future__ import annotations

import argparse
import json
from pathlib import Path

from cinegraph.adapters.catalogue import (
    JsonCatalogueManifestLoader,
    ReviewedSubtitleLedgerLoader,
)
from cinegraph.application.models.ingest_reviewed_corpus import (
    IngestReviewedCorpusCommand,
)
from cinegraph.bootstrap import CinegraphCompositionRoot
from cinegraph.config import CinegraphRuntimeSettings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or index ledger-approved private subtitle files."
    )
    parser.add_argument("--catalogue-manifest", required=True, type=Path)
    parser.add_argument("--review-ledger", required=True, type=Path)
    parser.add_argument("--reviewed-directory", required=True, type=Path)
    parser.add_argument("--env-file", default=Path(".env"), type=Path)
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

    runtime = CinegraphCompositionRoot(
        CinegraphRuntimeSettings(_env_file=arguments.env_file)
    )
    try:
        provisioning = runtime.provision_transcript_collection()
        result = runtime.reviewed_corpus_ingestion_service.execute(
            IngestReviewedCorpusCommand(batch=batch)
        )
    finally:
        runtime.close()
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
