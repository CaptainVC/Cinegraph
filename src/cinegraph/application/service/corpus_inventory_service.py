from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from uuid import UUID

from cinegraph.application.models.ingestion_job import (
    IngestionInventoryReport,
    IngestionJobPlanItem,
)
from cinegraph.common.error_messages import IngestionJobErrorMessages
from cinegraph.config import DEFAULT_CORPUS_LAYOUT
from cinegraph.domain.enums.enum import (
    CorpusInventoryReason,
    CorpusReadinessStatus,
    SourceReviewStatus,
)
from cinegraph.domain.models.catalogue.catalogue_manifest import CatalogueManifest
from cinegraph.domain.models.source.review_status import (
    is_final_source_review_status,
    is_source_version_approved,
)


class CorpusInventoryService:
    """Read-only reconciliation of trusted catalogue references and corpus artifacts."""

    def inspect(
        self,
        corpus_root: Path,
        manifest: CatalogueManifest,
        detail_output: Path | None = None,
    ) -> IngestionInventoryReport:
        root = corpus_root.resolve()
        items: list[IngestionJobPlanItem] = []
        for series in manifest.series:
            for season in series.seasons:
                try:
                    series_directory = _safe_series_directory(series.series_name)
                    season_root = _safe_under_root(
                        root,
                        root
                        / f"{series_directory}{DEFAULT_CORPUS_LAYOUT.season_directory_suffix.format(season_number=season.season_number)}",
                    )
                except ValueError:
                    items.extend(
                        IngestionJobPlanItem(
                            episode_id=episode.episode_id,
                            season_number=season.season_number,
                            episode_number=episode.episode_number,
                            status=CorpusReadinessStatus.INVALID,
                            reason_code=CorpusInventoryReason.UNSAFE_LOCATOR,
                            relative_locator="",
                            content_sha256=None,
                        )
                        for episode in season.episodes
                    )
                    continue
                for episode in season.episodes:
                    reviewed_name = episode.reviewed_subtitle_filename
                    if reviewed_name is None:
                        status = CorpusReadinessStatus.INVALID
                        reason = CorpusInventoryReason.MISSING_REVIEWED_LOCATOR
                        locator, digest = "", None
                    else:
                        try:
                            reviewed_path = _safe_under_root(
                                root,
                                season_root
                                / DEFAULT_CORPUS_LAYOUT.reviewed_directory_name
                                / reviewed_name,
                            )
                            raw_path = _safe_under_root(
                                root,
                                season_root
                                / reviewed_name.replace(
                                    DEFAULT_CORPUS_LAYOUT.reviewed_subtitle_suffix,
                                    DEFAULT_CORPUS_LAYOUT.raw_subtitle_suffix,
                                ),
                            )
                            aligned_path = _safe_under_root(
                                root,
                                season_root
                                / DEFAULT_CORPUS_LAYOUT.aligned_directory_name
                                / reviewed_name.replace(
                                    DEFAULT_CORPUS_LAYOUT.reviewed_subtitle_suffix,
                                    DEFAULT_CORPUS_LAYOUT.aligned_subtitle_suffix,
                                ),
                            )
                            ledger_path = _safe_under_root(
                                root,
                                season_root
                                / DEFAULT_CORPUS_LAYOUT.reviewed_directory_name
                                / DEFAULT_CORPUS_LAYOUT.review_ledger_filename,
                            )
                        except ValueError:
                            status = CorpusReadinessStatus.INVALID
                            reason = CorpusInventoryReason.UNSAFE_LOCATOR
                            locator, digest = "", None
                        else:
                            status, reason, locator, digest = self._classify(
                                root,
                                reviewed_path,
                                raw_path,
                                aligned_path,
                                ledger_path,
                                reviewed_name,
                            )
                    items.append(
                        IngestionJobPlanItem(
                            episode_id=episode.episode_id,
                            season_number=season.season_number,
                            episode_number=episode.episode_number,
                            status=status,
                            reason_code=reason,
                            relative_locator=locator,
                            content_sha256=digest,
                        )
                    )
        ordered = tuple(
            sorted(
                items,
                key=lambda item: (item.season_number, item.episode_number, str(item.episode_id)),
            )
        )
        report = IngestionInventoryReport(
            dict(
                sorted(
                    (status.value, count)
                    for status, count in Counter(item.status for item in ordered).items()
                )
            ),
            ordered,
        )
        if detail_output is not None:
            self._write_detail(root, detail_output, report)
        return report

    @staticmethod
    def _classify(
        root: Path,
        reviewed_path: Path,
        raw_path: Path,
        aligned_path: Path,
        ledger_path: Path,
        expected_reviewed_name: str,
    ) -> tuple[CorpusReadinessStatus, CorpusInventoryReason, str, str | None]:
        if reviewed_path.is_file():
            try:
                digest = hashlib.sha256(reviewed_path.read_bytes()).hexdigest()
            except OSError:
                return (
                    CorpusReadinessStatus.INVALID,
                    CorpusInventoryReason.ARTIFACT_UNREADABLE,
                    _relative(root, reviewed_path),
                    None,
                )
            if not ledger_path.is_file():
                return (
                    CorpusReadinessStatus.INVALID,
                    CorpusInventoryReason.REVIEW_LEDGER_MISSING,
                    _relative(root, reviewed_path),
                    digest,
                )
            try:
                ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
                records = ledger.get("records", [])
                matching = next(
                    (
                        record
                        for record in records
                        if record.get("reviewed_filename") == expected_reviewed_name
                    ),
                    None,
                )
                review_status = SourceReviewStatus(ledger.get("review_status"))
                if (
                    ledger.get("schema_version") == 1
                    and is_final_source_review_status(review_status)
                    and is_source_version_approved(review_status)
                    and matching is not None
                    and sum(
                        record.get("reviewed_filename") == expected_reviewed_name
                        for record in records
                    )
                    == 1
                    and matching.get("reviewed_sha256") == digest
                ):
                    return (
                        CorpusReadinessStatus.REVIEWED_READY,
                        CorpusInventoryReason.VERIFIED_REVIEW_LEDGER,
                        _relative(root, reviewed_path),
                        digest,
                    )
                return (
                    CorpusReadinessStatus.INVALID,
                    CorpusInventoryReason.REVIEW_LEDGER_HASH_OR_SCOPE_MISMATCH,
                    _relative(root, reviewed_path),
                    digest,
                )
            except (OSError, ValueError, TypeError, AttributeError):
                return (
                    CorpusReadinessStatus.INVALID,
                    CorpusInventoryReason.REVIEW_LEDGER_INVALID,
                    _relative(root, reviewed_path),
                    digest,
                )
        if aligned_path.is_file():
            try:
                digest = hashlib.sha256(aligned_path.read_bytes()).hexdigest()
            except OSError:
                return (
                    CorpusReadinessStatus.INVALID,
                    CorpusInventoryReason.ARTIFACT_UNREADABLE,
                    _relative(root, aligned_path),
                    None,
                )
            return (
                CorpusReadinessStatus.AWAITING_AUTOMATED_REVIEW,
                CorpusInventoryReason.SCRIPT_ALIGNED_WITHOUT_FINAL_REVIEW,
                _relative(root, aligned_path),
                digest,
            )
        if raw_path.is_file():
            try:
                digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
            except OSError:
                return (
                    CorpusReadinessStatus.INVALID,
                    CorpusInventoryReason.ARTIFACT_UNREADABLE,
                    _relative(root, raw_path),
                    None,
                )
            return (
                CorpusReadinessStatus.AWAITING_ALIGNMENT,
                CorpusInventoryReason.RAW_SUBTITLE_REQUIRES_SCRIPT_ALIGNMENT,
                _relative(root, raw_path),
                digest,
            )
        return (
            CorpusReadinessStatus.MISSING,
            CorpusInventoryReason.RAW_SUBTITLE_MISSING,
            _relative(root, raw_path),
            None,
        )

    @staticmethod
    def _write_detail(root: Path, output_path: Path, report: IngestionInventoryReport) -> None:
        target = _safe_under_root(root, output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "counts": report.counts,
            "items": [
                {
                    key: str(value) if isinstance(value, UUID) else value
                    for key, value in asdict(item).items()
                }
                for item in report.items
            ],
        }
        serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(serialized)
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, target)


def _relative(root: Path, path: Path) -> str:
    return _safe_under_root(root, path).relative_to(root).as_posix()


def _safe_under_root(root: Path, path: Path) -> Path:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(IngestionJobErrorMessages.INVENTORY_OUTPUT_MUST_BE_UNDER_ROOT) from error
    return resolved_path


def _safe_series_directory(series_name: str) -> str:
    directory = series_name.replace(" ", "_")
    if re.fullmatch(r"[A-Za-z0-9_-]+", directory) is None:
        raise ValueError("Series directory is not a safe locator.")
    return directory
