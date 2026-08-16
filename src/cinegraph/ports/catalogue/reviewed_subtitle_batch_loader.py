from pathlib import Path
from typing import Protocol

from cinegraph.application.models.ingest_reviewed_corpus import ReviewedSubtitleBatch
from cinegraph.domain.models.catalogue.catalogue_manifest import CatalogueManifest


class ReviewedSubtitleBatchLoader(Protocol):
    def load(
        self,
        manifest: CatalogueManifest,
        review_ledger_path: Path,
        reviewed_directory: Path,
    ) -> ReviewedSubtitleBatch: ...
