import hashlib
import json
import os
import stat
import warnings
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from scripts import ingest_private_corpus_workspace as worker

from cinegraph.application.models.ingest_reviewed_corpus import (
    IngestReviewedCorpusResult,
    ReviewedSubtitleBatch,
    ReviewedSubtitleBatchItem,
)
from cinegraph.domain.enums.enum import SourceReviewStatus
from cinegraph.domain.models.catalogue.catalogue_manifest import CatalogueManifest
from cinegraph.domain.models.catalogue.episode import Episode
from cinegraph.domain.models.catalogue.season import Season
from cinegraph.domain.models.catalogue.series import Series
from cinegraph.ports.catalogue import LoadedCatalogueManifest


def _catalogue() -> LoadedCatalogueManifest:
    episode = Episode(
        series_id=UUID(int=11),
        season_id=UUID(int=101),
        episode_id=UUID(int=1001),
        episode_number=1,
        episode_title="Pilot",
        reviewed_subtitle_filename="Pilot.reviewed.srt",
    )
    manifest = CatalogueManifest(
        schema_version=1,
        series=(
            Series(
                series_id=UUID(int=11),
                series_name="Modern Family",
                seasons=(
                    Season(
                        series_id=UUID(int=11),
                        season_id=UUID(int=101),
                        season_number=1,
                        episodes=(episode,),
                    ),
                ),
            ),
        ),
    )
    return LoadedCatalogueManifest(manifest=manifest, content_sha256="a" * 64)


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "workspace"
    reviewed = root / "Modern_Family - season 1.en" / "reviewed"
    reviewed.mkdir(parents=True)
    content = b"reviewed subtitle\n"
    (reviewed / "Pilot.reviewed.srt").write_bytes(content)
    (reviewed / "review-ledger.json").write_bytes(b"{}\n")
    data = {
        "path": "Modern_Family - season 1.en/reviewed/Pilot.reviewed.srt",
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    ledger = {
        "path": "Modern_Family - season 1.en/reviewed/review-ledger.json",
        "size": 3,
        "sha256": hashlib.sha256(b"{}\n").hexdigest(),
    }
    manifest = {
        "schema_version": 1,
        "purpose": "reviewed_ingestion",
        "season_number": 1,
        "files": sorted((data, ledger), key=lambda item: item["path"].encode()),
        "file_count": 2,
        "total_bytes": len(content) + 3,
        "source_catalogue_sha256": "a" * 64,
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    (root / "manifest.json").write_bytes(manifest_bytes)
    receipt = {
        "archive_sha256": "b" * 64,
        "catalogue_sha256": "a" * 64,
        "file_count": 2,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "protocol": 1,
        "purpose": "reviewed_ingestion",
        "schema_version": 1,
        "season_number": 1,
        "total_bytes": len(content) + 3,
    }
    (root / ".install-receipt.json").write_bytes(
        (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
    )
    catalogue_path = tmp_path / "catalogue.json"
    catalogue_path.write_bytes(b"catalogue")
    return root, catalogue_path


def _negative_case_dependencies(
    root: Path, *, invoked: list[bool]
) -> tuple[dict[str, object], LoadedCatalogueManifest]:
    loaded = _catalogue()
    episode = loaded.manifest.episode_refs()[0]
    source_path = root / "Modern_Family - season 1.en" / "reviewed" / "Pilot.reviewed.srt"
    item = ReviewedSubtitleBatchItem(
        episode=episode,
        episode_title="Modern Family: Pilot",
        source_path=source_path,
        content_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
        reviewed_by="reviewer",
        reviewed_at=datetime(2026, 1, 1, tzinfo=UTC),
        review_status=SourceReviewStatus.REVIEWED,
    )

    class CatalogueLoader:
        def load(self, path: Path) -> LoadedCatalogueManifest:
            return loaded

    class LedgerLoader:
        def load(
            self, manifest, ledger_path: Path, reviewed_directory: Path
        ) -> ReviewedSubtitleBatch:
            return ReviewedSubtitleBatch((item,))

    class Service:
        def execute(self, command) -> IngestReviewedCorpusResult:
            invoked.append(True)
            return IngestReviewedCorpusResult(outcomes=())

    class Root:
        reviewed_corpus_ingestion_service = Service()

        def __init__(self, settings) -> None:
            self.settings = settings

        def provision_transcript_collection(self) -> None:
            invoked.append(True)

        def close(self) -> None:
            pass

    return {
        "catalogue_loader_factory": CatalogueLoader,
        "ledger_loader_factory": LedgerLoader,
        "composition_root_factory": Root,
        "settings_factory": lambda **kwargs: kwargs,
    }, loaded


def test_ingest_workspace_uses_injected_dependencies_and_emits_aggregate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "_is_linux", lambda: False)
    root, catalogue_path = _workspace(tmp_path)
    loaded = _catalogue()
    episode = loaded.manifest.episode_refs()[0]
    item = ReviewedSubtitleBatchItem(
        episode=episode,
        episode_title="Modern Family: Pilot",
        source_path=root / "Modern_Family - season 1.en" / "reviewed" / "Pilot.reviewed.srt",
        content_sha256="c" * 64,
        reviewed_by="reviewer",
        reviewed_at=datetime(2026, 1, 1, tzinfo=UTC),
        review_status=SourceReviewStatus.REVIEWED,
    )

    class CatalogueLoader:
        def load(self, path: Path) -> LoadedCatalogueManifest:
            return loaded

    class LedgerLoader:
        def load(
            self, manifest, ledger_path: Path, reviewed_directory: Path
        ) -> ReviewedSubtitleBatch:
            return ReviewedSubtitleBatch((item,))

    class Service:
        def execute(self, command) -> IngestReviewedCorpusResult:
            return IngestReviewedCorpusResult(outcomes=())

    class Root:
        reviewed_corpus_ingestion_service = Service()

        def __init__(self, settings) -> None:
            self.settings = settings

        def provision_transcript_collection(self) -> None:
            pass

        def close(self) -> None:
            pass

    settings_values: dict[str, object] = {}

    def settings_factory(**kwargs: object) -> dict[str, object]:
        settings_values.update(kwargs)
        return kwargs

    result = worker.ingest_workspace(
        root,
        catalogue_path,
        catalogue_loader_factory=CatalogueLoader,
        ledger_loader_factory=LedgerLoader,
        composition_root_factory=Root,
        settings_factory=settings_factory,
    )

    assert result == {
        "mode": "ingest-reviewed",
        "purpose": "reviewed_ingestion",
        "season_number": 1,
        "file_count": 2,
        "total_bytes": 21,
        "episode_count": 1,
        "indexed_segment_count": 0,
    }
    assert settings_values["knowledge_root"] == catalogue_path.parent


def test_worker_suppresses_only_expected_dependency_warnings() -> None:
    with pytest.warns(UserWarning, match="unrelated warning") as caught_warnings:
        with worker._suppress_expected_worker_warnings():
            warnings.warn_explicit(
                "Api key is used with an insecure connection.",
                UserWarning,
                filename="composition_root.py",
                lineno=1,
                module="cinegraph.bootstrap.composition_root",
            )
            warnings.warn_explicit(
                "Cannot enable progress bars: environment variable `HF_HUB_DISABLE_PROGRESS_BARS=1` "
                "is set and has priority.",
                UserWarning,
                filename="tqdm.py",
                lineno=1,
                module="huggingface_hub.utils.tqdm",
            )
            warnings.warn_explicit(
                "unrelated warning",
                UserWarning,
                filename="other.py",
                lineno=1,
                module="other.module",
            )

    assert [str(warning.message) for warning in caught_warnings] == ["unrelated warning"]


def test_main_returns_generic_error_without_private_details(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(worker, "ingest_workspace", lambda: (_ for _ in ()).throw(
        RuntimeError("C:/private/corpus/review-ledger.json secret")
    ))

    assert worker.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error=private corpus ingestion failed\n"
    assert "C:/private/corpus" not in captured.err
    assert "review-ledger" not in captured.err


def test_extra_workspace_file_is_rejected_before_ingestion(tmp_path: Path) -> None:
    root, catalogue_path = _workspace(tmp_path)
    (root / "Modern_Family - season 1.en" / "reviewed" / "unexpected.srt").write_bytes(
        b"not in manifest"
    )
    invoked: list[bool] = []
    dependencies, _ = _negative_case_dependencies(root, invoked=invoked)

    with pytest.raises(worker.WorkspaceError):
        worker.ingest_workspace(root, catalogue_path, **dependencies)

    assert invoked == []


@pytest.mark.parametrize(
    "tamper", ["receipt_digest", "receipt_noncanonical", "manifest_noncanonical"]
)
def test_tampered_or_noncanonical_installed_metadata_is_rejected(
    tmp_path: Path, tamper: str
) -> None:
    root, catalogue_path = _workspace(tmp_path)
    receipt_path = root / ".install-receipt.json"
    manifest_path = root / "manifest.json"
    if tamper == "receipt_digest":
        receipt = json.loads(receipt_path.read_text())
        receipt["manifest_sha256"] = "c" * 64
        receipt_path.write_bytes(
            (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
        )
    elif tamper == "receipt_noncanonical":
        receipt_path.write_bytes(receipt_path.read_bytes() + b" ")
    else:
        manifest_path.write_bytes(b" " + manifest_path.read_bytes())
    invoked: list[bool] = []
    dependencies, _ = _negative_case_dependencies(root, invoked=invoked)

    with pytest.raises(worker.WorkspaceError):
        worker.ingest_workspace(root, catalogue_path, **dependencies)

    assert invoked == []


def test_linux_workspace_files_require_processing_uid_gid_and_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker, "_is_linux", lambda: True)
    valid = os.stat_result(
        (stat.S_IFREG | 0o600, 1, 1, 1, worker.PROCESSING_UID, worker.PROCESSING_GID, 1, 0, 0, 0)
    )
    worker._validate_file_metadata(valid)
    invalid_mode = os.stat_result(
        (stat.S_IFREG | 0o644, 1, 1, 1, worker.PROCESSING_UID, worker.PROCESSING_GID, 1, 0, 0, 0)
    )

    with pytest.raises(worker.WorkspaceError):
        worker._validate_file_metadata(invalid_mode)
