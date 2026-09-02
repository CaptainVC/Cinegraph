"""Build one deterministic, season-scoped private-corpus bundle."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from cinegraph.adapters.catalogue import JsonCatalogueManifestLoader
from cinegraph.application.service.corpus_inventory_service import CorpusInventoryService
from cinegraph.common.private_corpus_bundle import (
    PURPOSE_REVIEWED_INGESTION,
    PURPOSE_SPEAKER_REVIEW,
    BundleError,
    build_bundle,
)
from cinegraph.config import DEFAULT_CORPUS_LAYOUT, DEFAULT_SPEAKER_REVIEW_CONFIGURATION
from cinegraph.domain.enums.enum import CorpusReadinessStatus
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.catalogue.season import Season
from cinegraph.domain.models.catalogue.series import Series
from cinegraph.ports.catalogue import LoadedCatalogueManifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--purpose", choices=(PURPOSE_REVIEWED_INGESTION, PURPOSE_SPEAKER_REVIEW), required=True
    )
    parser.add_argument("--catalogue", required=True, type=Path)
    parser.add_argument("--season", required=True, type=int)
    return parser


def _safe_series_directory(series_name: str) -> str:
    directory = series_name.replace(" ", "_")
    if re.fullmatch(r"[A-Za-z0-9_-]+", directory) is None:
        raise BundleError("catalogue series locator is invalid")
    return directory


def _selected_season(
    loaded: LoadedCatalogueManifest, season_number: int
) -> tuple[Series, Season]:
    manifest = loaded.manifest
    seasons = [
        (series, season)
        for series in manifest.series
        for season in series.seasons
        if season.season_number == season_number
    ]
    if len(seasons) != 1:
        raise BundleError("catalogue must identify exactly one series season")
    return seasons[0]


def _selection(
    *,
    knowledge_root: Path,
    loaded: LoadedCatalogueManifest,
    purpose: str,
    season_number: int,
) -> list[str]:
    series, season = _selected_season(loaded, season_number)
    series_directory = _safe_series_directory(series.series_name)
    season_directory = (
        f"{series_directory}"
        f"{DEFAULT_CORPUS_LAYOUT.season_directory_suffix.format(season_number=season_number)}"
    )
    season_root = Path(season_directory)
    if purpose == PURPOSE_REVIEWED_INGESTION:
        expected_ledger = (
            season_root
            / DEFAULT_CORPUS_LAYOUT.reviewed_directory_name
            / DEFAULT_CORPUS_LAYOUT.review_ledger_filename
        )
        report = CorpusInventoryService().inspect(knowledge_root, loaded.manifest)
        season_items = [item for item in report.items if item.season_number == season_number]
        if len(season_items) != len(season.episodes) or any(
            item.status is not CorpusReadinessStatus.REVIEWED_READY for item in season_items
        ):
            raise BundleError("selected season is not completely reviewed_ready")
        selected = [item.relative_locator for item in season_items]
        selected.append(expected_ledger.as_posix())
        return selected

    script = Path(
        DEFAULT_SPEAKER_REVIEW_CONFIGURATION.script_pdf_filename_template.format(
            season=season_number
        )
    )
    aligned: list[str] = []
    for episode in sorted(season.episodes, key=lambda item: item.episode_number):
        filename = episode.reviewed_subtitle_filename
        if not filename or not filename.endswith(DEFAULT_CORPUS_LAYOUT.reviewed_subtitle_suffix):
            raise BundleError("catalogue does not provide complete script-aligned selection")
        aligned_name = filename[: -len(DEFAULT_CORPUS_LAYOUT.reviewed_subtitle_suffix)] + (
            DEFAULT_CORPUS_LAYOUT.aligned_subtitle_suffix
        )
        aligned.append(
            (season_root / DEFAULT_CORPUS_LAYOUT.aligned_directory_name / aligned_name).as_posix()
        )
    return [script.as_posix(), *aligned]


def _catalogue_is_external(catalogue: Path, knowledge_root: Path) -> None:
    if not catalogue.is_file() or catalogue.is_symlink():
        raise BundleError("catalogue must be a regular file")
    try:
        catalogue.resolve(strict=True).relative_to(knowledge_root.resolve(strict=True))
    except ValueError:
        return
    raise BundleError("catalogue must be outside the knowledge root")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.season < 1:
            raise BundleError("season number must be a positive integer")
        _catalogue_is_external(args.catalogue, args.knowledge_root)
        loaded = JsonCatalogueManifestLoader().load(args.catalogue)
        selected = _selection(
            knowledge_root=args.knowledge_root,
            loaded=loaded,
            purpose=args.purpose,
            season_number=args.season,
        )
        result = build_bundle(
            source_root=args.knowledge_root,
            output_archive=args.output,
            purpose=args.purpose,
            selected_paths=selected,
            catalogue_sha256=loaded.content_sha256,
            season_number=args.season,
        )
    except BundleError as error:
        print(f"error={error}", file=sys.stderr)
        return 2
    except (InvalidModelError, OSError, RuntimeError, ValueError):
        # Never echo operator paths, source names, or provider exception text.
        print("error=bundle build failed", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "purpose": result.purpose,
                "season_number": result.season_number,
                "file_count": result.file_count,
                "total_bytes": result.total_bytes,
                "archive_bytes": result.archive_bytes,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
