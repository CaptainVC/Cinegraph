"""Report safe aggregate readiness for the governed local corpus."""

import argparse
from pathlib import Path

from cinegraph.adapters.catalogue import JsonCatalogueManifestLoader
from cinegraph.application.service.corpus_inventory_service import CorpusInventoryService


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--catalogue", type=Path, default=Path("knowledge/catalogue.json"))
    parser.add_argument("--detail-output", type=Path)
    args = parser.parse_args()
    loaded = JsonCatalogueManifestLoader().load(args.catalogue)
    report = CorpusInventoryService().inspect(args.corpus_root, loaded.manifest, args.detail_output)
    print(" ".join(f"{key}={value}" for key, value in sorted(report.counts.items())))


if __name__ == "__main__":
    main()
