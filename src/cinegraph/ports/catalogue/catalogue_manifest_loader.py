from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cinegraph.domain.models.catalogue.catalogue_manifest import CatalogueManifest


@dataclass(frozen=True, slots=True)
class LoadedCatalogueManifest:
    manifest: CatalogueManifest
    content_sha256: str


class CatalogueManifestLoader(Protocol):
    def load(self, path: Path) -> LoadedCatalogueManifest: ...
