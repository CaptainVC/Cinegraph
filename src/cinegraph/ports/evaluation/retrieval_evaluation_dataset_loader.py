from pathlib import Path
from typing import Protocol

from cinegraph.application.models.retrieval_evaluation import (
    RetrievalEvaluationDataset,
)
from cinegraph.domain.models.catalogue.catalogue_manifest import CatalogueManifest


class RetrievalEvaluationDatasetLoader(Protocol):
    def load(
        self,
        manifest: CatalogueManifest,
        path: Path,
    ) -> RetrievalEvaluationDataset: ...
