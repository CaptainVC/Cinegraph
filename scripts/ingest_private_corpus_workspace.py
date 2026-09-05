"""Run the bounded, unprivileged reviewed-corpus ingestion workspace."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import warnings
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Final, Iterator, NoReturn, cast

from cinegraph.adapters.catalogue import (
    JsonCatalogueManifestLoader,
    ReviewedSubtitleLedgerLoader,
)
from cinegraph.application.models.ingest_reviewed_corpus import (
    IngestReviewedCorpusCommand,
    IngestReviewedCorpusResult,
    ReviewedSubtitleBatch,
)
from cinegraph.bootstrap import CinegraphCompositionRoot
from cinegraph.common.private_corpus_bundle import (
    BundleError,
    BundleFile,
    _decode_manifest,
)
from cinegraph.common.private_corpus_policy import (
    DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION,
)
from cinegraph.config import DEFAULT_CORPUS_LAYOUT, CinegraphRuntimeSettings
from cinegraph.config.corpus_worker import (
    CORPUS_WORKER_EMBEDDING_INFERENCE_THREADS,
    CORPUS_WORKER_EMBEDDING_MAX_BATCH_SIZE,
    CORPUS_WORKER_WARNING_FILTERS,
)
from cinegraph.ports.catalogue import LoadedCatalogueManifest

WORKSPACE_ROOT: Final = Path("/private-corpus")
CATALOGUE_PATH: Final = Path("/app/knowledge/catalogue.json")
MANIFEST_FILENAME: Final = DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION.manifest_filename
RECEIPT_FILENAME: Final = ".install-receipt.json"
REVIEW_LEDGER_FILENAME: Final = DEFAULT_CORPUS_LAYOUT.review_ledger_filename
EXPECTED_PURPOSE: Final = DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION.purpose_reviewed_ingestion
EXPECTED_SEASON: Final = 1
PROCESSING_UID: Final = 10001
PROCESSING_GID: Final = 10001
MAX_OUTPUT_BYTES: Final = 4096
SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")


@contextmanager
def _suppress_expected_worker_warnings() -> Iterator[None]:
    """Hide only dependency warnings known to be safe for this one-shot."""

    with warnings.catch_warnings():
        for message, module in CORPUS_WORKER_WARNING_FILTERS:
            warnings.filterwarnings(
                "ignore",
                message=message,
                category=UserWarning,
                module=module,
            )
        yield


class WorkspaceError(RuntimeError):
    """A path-free workspace validation or ingestion failure."""


def _fail() -> NoReturn:
    raise WorkspaceError("private corpus workspace rejected")


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _metadata_is_directory(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and not bool((getattr(metadata, "st_file_attributes", 0) or 0) & 0x400)
    )


def _validate_directory(path: Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise WorkspaceError("private corpus workspace unavailable") from error
    if not _metadata_is_directory(metadata):
        _fail()
    if _is_linux() and (
        metadata.st_uid != PROCESSING_UID
        or metadata.st_gid != PROCESSING_GID
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail()
    return metadata


def _validate_file_metadata(
    metadata: os.stat_result, *, enforce_processing_owner: bool = True
) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or bool((getattr(metadata, "st_file_attributes", 0) or 0) & 0x400)
        or metadata.st_nlink != 1
        or metadata.st_size <= 0
    ):
        _fail()
    if enforce_processing_owner and _is_linux() and (
        metadata.st_uid != PROCESSING_UID
        or metadata.st_gid != PROCESSING_GID
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        _fail()


def _stable_bytes(
    path: Path, *, maximum: int, enforce_processing_owner: bool = True
) -> tuple[bytes, os.stat_result]:
    try:
        before = path.lstat()
        _validate_file_metadata(before, enforce_processing_owner=enforce_processing_owner)
        if before.st_size > maximum:
            _fail()
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            _validate_file_metadata(opened, enforce_processing_owner=enforce_processing_owner)
            content = stream.read(maximum + 1)
        after = path.lstat()
        _validate_file_metadata(after, enforce_processing_owner=enforce_processing_owner)
    except WorkspaceError:
        raise
    except OSError as error:
        raise WorkspaceError("private corpus file unavailable") from error
    def identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_nlink,
        )
    if (
        identity(before) != identity(opened)
        or identity(opened) != identity(after)
        or len(content) != opened.st_size
        or len(content) > maximum
    ):
        _fail()
    return content, after


def _inventory(root: Path) -> tuple[set[str], set[str]]:
    _validate_directory(root)
    files: set[str] = set()
    directories: set[str] = {"."}
    try:
        walker = os.walk(root, topdown=True, followlinks=False)
        for directory, names, filenames in walker:
            current = Path(directory)
            relative_directory = current.relative_to(root).as_posix() or "."
            metadata = current.lstat()
            if not _metadata_is_directory(metadata):
                _fail()
            directories.add(relative_directory)
            for name in names:
                candidate = current / name
                child_metadata = candidate.lstat()
                if not _metadata_is_directory(child_metadata):
                    _fail()
                directories.add(candidate.relative_to(root).as_posix())
            for name in filenames:
                candidate = current / name
                metadata = candidate.lstat()
                _validate_file_metadata(metadata)
                files.add(candidate.relative_to(root).as_posix())
    except WorkspaceError:
        raise
    except (OSError, ValueError) as error:
        raise WorkspaceError("private corpus workspace unavailable") from error
    return files, directories


def _decode_receipt(raw: bytes) -> dict[str, object]:
    required = {
        "archive_sha256",
        "catalogue_sha256",
        "file_count",
        "manifest_sha256",
        "protocol",
        "purpose",
        "schema_version",
        "season_number",
        "total_bytes",
    }

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate receipt key")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise WorkspaceError("private corpus receipt rejected") from error
    if not isinstance(value, dict) or set(value) != required:
        _fail()
    canonical = (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if canonical != raw:
        _fail()
    for key in ("archive_sha256", "catalogue_sha256", "manifest_sha256"):
        if not isinstance(value[key], str) or SHA256_PATTERN.fullmatch(value[key]) is None:
            _fail()
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or type(value["protocol"]) is not int
        or value["protocol"] != 1
        or value["purpose"] != EXPECTED_PURPOSE
        or type(value["season_number"]) is not int
        or value["season_number"] != EXPECTED_SEASON
        or type(value["file_count"]) is not int
        or value["file_count"] <= 0
        or type(value["total_bytes"]) is not int
        or value["total_bytes"] <= 0
    ):
        _fail()
    return value


def _expected_paths(loaded: LoadedCatalogueManifest) -> set[str]:
    selected = [
        (series, season)
        for series in loaded.manifest.series
        for season in series.seasons
        if season.season_number == EXPECTED_SEASON
    ]
    if len(selected) != 1:
        _fail()
    series, season = selected[0]
    if any(episode.reviewed_subtitle_filename is None for episode in season.episodes):
        _fail()
    series_directory = series.series_name.replace(" ", "_")
    season_directory = (
        series_directory
        + DEFAULT_CORPUS_LAYOUT.season_directory_suffix.format(
            season_number=EXPECTED_SEASON
        )
    )
    reviewed_directory = PurePosixPath(
        season_directory, DEFAULT_CORPUS_LAYOUT.reviewed_directory_name
    )
    result = {str(reviewed_directory / REVIEW_LEDGER_FILENAME)}
    result.update(
        str(reviewed_directory / cast(str, episode.reviewed_subtitle_filename))
        for episode in season.episodes
    )
    return result


def _validate_workspace(
    workspace_root: Path,
    catalogue_path: Path,
    *,
    catalogue_loader: JsonCatalogueManifestLoader,
    ledger_loader: ReviewedSubtitleLedgerLoader,
) -> tuple[dict[str, object], LoadedCatalogueManifest, Path, ReviewedSubtitleBatch]:
    files_before, directories_before = _inventory(workspace_root)
    manifest_path = workspace_root / MANIFEST_FILENAME
    receipt_path = workspace_root / RECEIPT_FILENAME
    manifest_bytes, _ = _stable_bytes(
        manifest_path,
        maximum=DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION.max_manifest_bytes,
    )
    try:
        manifest = _decode_manifest(manifest_bytes)
    except (BundleError, TypeError, ValueError) as error:
        raise WorkspaceError("private corpus manifest rejected") from error
    if (
        manifest.get("purpose") != EXPECTED_PURPOSE
        or manifest.get("season_number") != EXPECTED_SEASON
    ):
        _fail()
    manifest_files = tuple(
        BundleFile(item["path"], item["size"], item["sha256"])
        for item in cast(list[dict[str, object]], manifest["files"])
    )
    expected_manifest_files = {item.path for item in manifest_files}
    expected_files = {MANIFEST_FILENAME, RECEIPT_FILENAME, *expected_manifest_files}
    expected_directories = {"."}
    for item in expected_manifest_files:
        parent = PurePosixPath(item).parent
        while parent != PurePosixPath("."):
            expected_directories.add(parent.as_posix())
            parent = parent.parent
    if files_before != expected_files or directories_before != expected_directories:
        _fail()

    receipt_bytes, _ = _stable_bytes(
        receipt_path,
        maximum=DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION.max_file_bytes,
    )
    receipt = _decode_receipt(receipt_bytes)
    if (
        receipt["manifest_sha256"] != hashlib.sha256(manifest_bytes).hexdigest()
        or receipt["catalogue_sha256"] != manifest["source_catalogue_sha256"]
        or receipt["file_count"] != manifest["file_count"]
        or receipt["total_bytes"] != manifest["total_bytes"]
    ):
        _fail()

    for item in manifest_files:
        content, _ = _stable_bytes(
            workspace_root.joinpath(*PurePosixPath(item.path).parts),
            maximum=DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION.max_file_bytes,
        )
        if len(content) != item.size or hashlib.sha256(content).hexdigest() != item.sha256:
            _fail()

    catalogue_bytes, _ = _stable_bytes(
        catalogue_path, maximum=1024 * 1024, enforce_processing_owner=False
    )
    try:
        loaded = catalogue_loader.load(catalogue_path)
    except Exception as error:
        raise WorkspaceError("active catalogue rejected") from error
    catalogue_after, _ = _stable_bytes(
        catalogue_path, maximum=1024 * 1024, enforce_processing_owner=False
    )
    if catalogue_after != catalogue_bytes:
        _fail()
    if loaded.content_sha256 != manifest["source_catalogue_sha256"]:
        _fail()
    if expected_manifest_files != _expected_paths(loaded):
        _fail()
    ledgers = [
        item
        for item in expected_manifest_files
        if PurePosixPath(item).name == REVIEW_LEDGER_FILENAME
    ]
    if len(ledgers) != 1:
        _fail()
    ledger_path = workspace_root.joinpath(*PurePosixPath(ledgers[0]).parts)
    reviewed_directory = ledger_path.parent
    try:
        batch = ledger_loader.load(loaded.manifest, ledger_path, reviewed_directory)
    except Exception as error:
        raise WorkspaceError("review ledger rejected") from error
    if len(batch.items) != len(_expected_paths(loaded)) - 1:
        _fail()
    # The ledger loader reads every subtitle; verify those bytes again before
    # handing the batch to the application so a same-size rewrite cannot cross
    # the validation/ingestion boundary.
    for item in manifest_files:
        content, _ = _stable_bytes(
            workspace_root.joinpath(*PurePosixPath(item.path).parts),
            maximum=DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION.max_file_bytes,
        )
        if len(content) != item.size or hashlib.sha256(content).hexdigest() != item.sha256:
            _fail()
    files_after, directories_after = _inventory(workspace_root)
    if files_after != files_before or directories_after != directories_before:
        _fail()
    return manifest, loaded, ledger_path, batch


def ingest_workspace(
    workspace_root: Path | None = None,
    catalogue_path: Path | None = None,
    *,
    catalogue_loader_factory: Callable[[], JsonCatalogueManifestLoader] | None = None,
    ledger_loader_factory: Callable[[], ReviewedSubtitleLedgerLoader] | None = None,
    composition_root_factory: Callable[[CinegraphRuntimeSettings], CinegraphCompositionRoot]
    | None = None,
    settings_factory: Callable[..., CinegraphRuntimeSettings] | None = None,
) -> dict[str, object]:
    """Validate one exact workspace and ingest it through the application root."""
    # Keep the loader factory and root factory injectable for tests and offline checks.
    workspace_root = WORKSPACE_ROOT if workspace_root is None else workspace_root
    catalogue_path = CATALOGUE_PATH if catalogue_path is None else catalogue_path
    catalogue_loader_factory = (
        JsonCatalogueManifestLoader
        if catalogue_loader_factory is None
        else catalogue_loader_factory
    )
    ledger_loader_factory = (
        ReviewedSubtitleLedgerLoader
        if ledger_loader_factory is None
        else ledger_loader_factory
    )
    composition_root_factory = (
        CinegraphCompositionRoot
        if composition_root_factory is None
        else composition_root_factory
    )
    settings_factory = CinegraphRuntimeSettings if settings_factory is None else settings_factory
    catalogue_loader = catalogue_loader_factory()
    manifest, loaded, ledger_path, batch = _validate_workspace(
        workspace_root,
        catalogue_path,
        catalogue_loader=catalogue_loader,
        ledger_loader=ledger_loader_factory(),
    )
    if len(batch.items) != len(cast(list[object], manifest["files"])) - 1:
        _fail()
    with _suppress_expected_worker_warnings():
        settings = settings_factory(
            _env_file=None,
            knowledge_root=catalogue_path.parent,
            embedding_max_batch_size=CORPUS_WORKER_EMBEDDING_MAX_BATCH_SIZE,
            embedding_inference_threads=CORPUS_WORKER_EMBEDDING_INFERENCE_THREADS,
        )
        runtime = composition_root_factory(settings)
        try:
            runtime.provision_transcript_collection()
            result = runtime.reviewed_corpus_ingestion_service.execute(
                IngestReviewedCorpusCommand(batch=batch)
            )
        finally:
            runtime.close()
    if not isinstance(result, IngestReviewedCorpusResult):
        # Keep this check permissive for injectable test doubles while requiring the
        # one application result property used by the public aggregate.
        indexed_segment_count = getattr(result, "indexed_segment_count", None)
    else:
        indexed_segment_count = result.indexed_segment_count
    if type(indexed_segment_count) is not int or indexed_segment_count < 0:
        _fail()
    aggregate = {
        "mode": "ingest-reviewed",
        "purpose": EXPECTED_PURPOSE,
        "season_number": EXPECTED_SEASON,
        "file_count": manifest["file_count"],
        "total_bytes": manifest["total_bytes"],
        "episode_count": len(batch.items),
        "indexed_segment_count": indexed_segment_count,
    }
    encoded = (
        json.dumps(aggregate, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        _fail()
    return aggregate


def main() -> int:
    try:
        result = ingest_workspace()
    except Exception:
        sys.stderr.write("error=private corpus ingestion failed\n")
        return 2
    sys.stdout.buffer.write(
        (
            json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
