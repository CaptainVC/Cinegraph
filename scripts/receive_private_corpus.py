"""Receive, verify, and atomically publish one Dev private-corpus bundle."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Mapping, cast
from uuid import UUID

# The privileged wrapper launches Python in isolated mode. Add only this exact,
# root-controlled release and its source tree; never inherit cwd or PYTHONPATH.
_RELEASE_ROOT = Path(__file__).resolve().parents[1]
for _import_root in (_RELEASE_ROOT, _RELEASE_ROOT / "src"):
    if os.fspath(_import_root) not in sys.path:
        sys.path.insert(0, os.fspath(_import_root))

from cinegraph.common.private_corpus_bundle import (  # noqa: E402
    BundleError,
    BundleFile,
    _decode_manifest,
    _rename_no_replace,
    _validate_name,
    stage_bundle,
    verify_bundle,
)
from cinegraph.common.private_corpus_policy import (  # noqa: E402
    DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION,
)
from scripts import private_corpus_host_contract as host_contract  # noqa: E402

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RELEASE_NAME_RE = re.compile(r"^[0-9a-f]{40}$")
_APPROVED_REVIEW_STATUSES = frozenset({"automated_reviewed", "hybrid_reviewed", "reviewed"})
_CATALOGUE_ROOT_KEYS = frozenset({"schema_version", "series"})
_CATALOGUE_SERIES_KEYS = frozenset({"series_id", "series_name", "seasons"})
_CATALOGUE_SEASON_KEYS = frozenset({"season_id", "season_number", "episodes"})
_CATALOGUE_EPISODE_REQUIRED_KEYS = frozenset({"episode_id", "episode_number", "episode_title"})
_CATALOGUE_EPISODE_OPTIONAL_KEYS = frozenset(
    {"reviewed_subtitle_filename", "runtime_seconds", "synopsis"}
)
_LEDGER_KEYS = frozenset(
    {"schema_version", "review_status", "reviewed_by", "reviewed_at", "records"}
)
_LEDGER_RECORD_KEYS = frozenset(
    {
        "candidate_filename",
        "reviewed_filename",
        "candidate_sha256",
        "reviewed_sha256",
        "promoted_question_mark_labels",
        "removed_redaction_lines",
        "removed_cue_numbers",
    }
)


class TransferError(RuntimeError):
    """A private, path-free transfer rejection."""


@dataclass(frozen=True, slots=True)
class TransferHeader:
    archive_bytes: int
    archive_sha256: str
    protocol: int


@dataclass(frozen=True, slots=True)
class CatalogueSnapshot:
    content_sha256: str
    reviewed_filenames_by_season: Mapping[int, tuple[str, ...]]


def _root_owned(result: os.stat_result) -> bool:
    return os.name == "nt" or (result.st_uid == 0 and result.st_gid == 0)


def _mode_is(result: os.stat_result, expected: int) -> bool:
    return os.name == "nt" or stat.S_IMODE(result.st_mode) == expected


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise TransferError("invalid_header")
        value[key] = item
    return value


def read_transfer_header(stream: BinaryIO) -> TransferHeader:
    """Read one bounded canonical protocol header without consuming archive bytes."""

    raw = stream.readline(host_contract.HEADER_MAX_BYTES + 1)
    if (
        not raw
        or len(raw) > host_contract.HEADER_MAX_BYTES
        or not raw.endswith(b"\n")
        or raw.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"))
    ):
        raise TransferError("invalid_header")
    try:
        decoded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, TransferError) as error:
        raise TransferError("invalid_header") from error
    if (
        not isinstance(decoded, dict)
        or set(decoded) != host_contract.HEADER_KEYS
        or host_contract.canonical_json(decoded) != raw
    ):
        raise TransferError("invalid_header")
    archive_bytes = decoded["archive_bytes"]
    archive_sha256 = decoded["archive_sha256"]
    protocol = decoded["protocol"]
    policy = DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION
    if (
        type(archive_bytes) is not int
        or archive_bytes <= 0
        or archive_bytes > policy.max_archive_bytes
        or not isinstance(archive_sha256, str)
        or not _SHA256_RE.fullmatch(archive_sha256)
        or type(protocol) is not int
        or protocol != host_contract.TRANSFER_PROTOCOL_VERSION
    ):
        raise TransferError("invalid_header")
    return TransferHeader(archive_bytes, archive_sha256, protocol)


def _require_private_root(path: Path, mode: int = 0o700) -> os.stat_result:
    try:
        result = path.lstat()
    except OSError as error:
        raise TransferError("host_not_ready") from error
    if (
        not stat.S_ISDIR(result.st_mode)
        or stat.S_ISLNK(result.st_mode)
        or not _root_owned(result)
        or not _mode_is(result, mode)
        or path.resolve(strict=True) != Path(os.path.abspath(path))
    ):
        raise TransferError("host_not_ready")
    return result


def _require_host_hierarchy() -> None:
    expected = (
        (host_contract.DEPLOY_ROOT, 0o750),
        (host_contract.RELEASES_ROOT, 0o750),
        (host_contract.SHARED_ROOT, 0o750),
        (host_contract.PRIVATE_CORPUS_ROOT, 0o700),
        (host_contract.DEV_PRIVATE_CORPUS_ROOT, 0o700),
        (host_contract.TRANSACTIONS_ROOT, 0o700),
        (host_contract.OBJECTS_ROOT, 0o700),
        (host_contract.QUARANTINE_ROOT, 0o700),
    )
    if (
        host_contract.RELEASES_ROOT.parent != host_contract.DEPLOY_ROOT
        or host_contract.SHARED_ROOT.parent != host_contract.DEPLOY_ROOT
        or host_contract.PRIVATE_CORPUS_ROOT.parent != host_contract.SHARED_ROOT
        or host_contract.DEV_PRIVATE_CORPUS_ROOT.parent != host_contract.PRIVATE_CORPUS_ROOT
        or any(
            path.parent != host_contract.DEV_PRIVATE_CORPUS_ROOT
            for path in (
                host_contract.TRANSACTIONS_ROOT,
                host_contract.OBJECTS_ROOT,
                host_contract.QUARANTINE_ROOT,
            )
        )
    ):
        raise TransferError("host_not_ready")
    metadata = tuple(_require_private_root(path, mode) for path, mode in expected)
    private_devices = {item.st_dev for item in metadata[3:]}
    if len(private_devices) != 1:
        raise TransferError("host_not_ready")


def _require_capacity(header: TransferHeader) -> None:
    policy = DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION
    try:
        statvfs = getattr(os, "statvfs")
        capacity = statvfs(host_contract.TRANSACTIONS_ROOT)
    except OSError as error:
        raise TransferError("host_capacity") from error
    available_bytes = capacity.f_bavail * capacity.f_frsize
    peak_bytes = (
        header.archive_bytes * 2 + policy.max_total_bytes + host_contract.TRANSFER_OVERHEAD_BYTES
    )
    required_inodes = host_contract.MIN_FREE_INODES_AFTER_TRANSFER + policy.max_file_count * 2 + 32
    if (
        available_bytes - peak_bytes < host_contract.MIN_FREE_BYTES_AFTER_TRANSFER
        or capacity.f_favail < required_inodes
    ):
        raise TransferError("host_capacity")


def _write_received_archive(stream: BinaryIO, path: Path, header: TransferHeader) -> None:
    descriptor = -1
    digest = hashlib.sha256()
    remaining = header.archive_bytes
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            descriptor = -1
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise TransferError("incomplete_stream")
                output.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
            if stream.read(1):
                raise TransferError("trailing_stream")
            output.flush()
            os.fsync(output.fileno())
    except OSError as error:
        raise TransferError("receive_failed") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if digest.hexdigest() != header.archive_sha256:
        raise TransferError("digest_mismatch")
    result = path.lstat()
    if (
        not stat.S_ISREG(result.st_mode)
        or stat.S_ISLNK(result.st_mode)
        or not _root_owned(result)
        or not _mode_is(result, 0o600)
        or result.st_nlink != 1
        or result.st_size != header.archive_bytes
    ):
        raise TransferError("receive_failed")


def _regular_root_file(path: Path, *, mode: int, max_bytes: int) -> bytes:
    descriptor = -1
    try:
        before = path.lstat()
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if opened.st_size <= 0 or opened.st_size > max_bytes:
            raise TransferError("object_integrity")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            content = stream.read(max_bytes + 1)
        after = path.lstat()
    except OSError as error:
        raise TransferError("object_integrity") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    identities = {
        (
            item.st_dev,
            item.st_ino,
            item.st_size,
            item.st_mtime_ns,
            item.st_nlink,
            item.st_mode,
            item.st_uid,
            item.st_gid,
        )
        for item in (before, opened, after)
    }
    if (
        len(identities) != 1
        or not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or not _root_owned(before)
        or not _mode_is(before, mode)
        or before.st_nlink != 1
        or len(content) != before.st_size
    ):
        raise TransferError("object_integrity")
    return content


def _strict_json(raw: bytes, *, error_code: str) -> object:
    if not raw or raw.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        raise TransferError(error_code)

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        decoded: dict[str, object] = {}
        for key, value in pairs:
            if key in decoded:
                raise ValueError
            decoded[key] = value
        return decoded

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise TransferError(error_code) from error


def _strict_uuid(value: object) -> str:
    if not isinstance(value, str):
        raise TransferError("catalogue_mismatch")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise TransferError("catalogue_mismatch") from error
    if str(parsed) != value:
        raise TransferError("catalogue_mismatch")
    return value


def _parse_catalogue(raw: bytes) -> CatalogueSnapshot:
    decoded = _strict_json(raw, error_code="catalogue_mismatch")
    if not isinstance(decoded, dict) or set(decoded) != _CATALOGUE_ROOT_KEYS:
        raise TransferError("catalogue_mismatch")
    if type(decoded["schema_version"]) is not int or decoded["schema_version"] != 1:
        raise TransferError("catalogue_mismatch")
    series_items = decoded["series"]
    if not isinstance(series_items, list) or len(series_items) != 1:
        raise TransferError("catalogue_mismatch")
    series = series_items[0]
    if not isinstance(series, dict) or set(series) != _CATALOGUE_SERIES_KEYS:
        raise TransferError("catalogue_mismatch")
    if (
        _strict_uuid(series["series_id"]) != host_contract.CANONICAL_SERIES_ID
        or series["series_name"] != host_contract.CANONICAL_SERIES_NAME
    ):
        raise TransferError("catalogue_mismatch")
    seasons = series["seasons"]
    if not isinstance(seasons, list) or len(seasons) != 2:
        raise TransferError("catalogue_mismatch")

    canonical_seasons: list[dict[str, object]] = []
    filenames_by_season: dict[int, tuple[str, ...]] = {}
    season_ids: set[str] = set()
    episode_ids: set[str] = set()
    global_filenames: set[str] = set()
    for season in seasons:
        if not isinstance(season, dict) or set(season) != _CATALOGUE_SEASON_KEYS:
            raise TransferError("catalogue_mismatch")
        season_id = _strict_uuid(season["season_id"])
        season_number = season["season_number"]
        episodes = season["episodes"]
        if (
            season_id in season_ids
            or type(season_number) is not int
            or season_number not in host_contract.ALLOWED_SCHEMA_V1_SEASONS
            or season_number in filenames_by_season
            or not isinstance(episodes, list)
            or not episodes
        ):
            raise TransferError("catalogue_mismatch")
        season_ids.add(season_id)
        canonical_episodes: list[dict[str, object]] = []
        season_numbers: set[int] = set()
        season_filenames: list[str] = []
        for episode in episodes:
            if (
                not isinstance(episode, dict)
                or not _CATALOGUE_EPISODE_REQUIRED_KEYS.issubset(episode)
                or not set(episode).issubset(
                    _CATALOGUE_EPISODE_REQUIRED_KEYS | _CATALOGUE_EPISODE_OPTIONAL_KEYS
                )
            ):
                raise TransferError("catalogue_mismatch")
            episode_id = _strict_uuid(episode["episode_id"])
            episode_number = episode["episode_number"]
            title = episode["episode_title"]
            filename = episode.get("reviewed_subtitle_filename")
            synopsis = episode.get("synopsis")
            runtime = episode.get("runtime_seconds")
            if (
                episode_id in episode_ids
                or type(episode_number) is not int
                or episode_number < 1
                or episode_number in season_numbers
                or not isinstance(title, str)
                or not title
                or title.strip() != title
                or not isinstance(filename, str)
                or not filename.endswith(
                    DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION.reviewed_subtitle_suffix
                )
                or filename.casefold() in global_filenames
                or (synopsis is not None and not isinstance(synopsis, str))
                or (runtime is not None and (type(runtime) is not int or runtime < 1))
            ):
                raise TransferError("catalogue_mismatch")
            try:
                if filename != PurePosixPath(filename).name or "\\" in filename:
                    raise BundleError("catalogue filename is not portable")
                _validate_name(filename)
            except BundleError as error:
                raise TransferError("catalogue_mismatch") from error
            episode_ids.add(episode_id)
            season_numbers.add(episode_number)
            global_filenames.add(filename.casefold())
            season_filenames.append(filename)
            canonical_episodes.append(
                {
                    "episode_id": episode_id,
                    "episode_number": episode_number,
                    "episode_title": title,
                    "reviewed_subtitle_filename": filename,
                    "runtime_seconds": runtime,
                    "synopsis": synopsis,
                }
            )
        canonical_episodes.sort(key=lambda item: cast(int, item["episode_number"]))
        filenames_by_season[season_number] = tuple(
            str(item["reviewed_subtitle_filename"]) for item in canonical_episodes
        )
        canonical_seasons.append(
            {
                "season_id": season_id,
                "season_number": season_number,
                "episodes": canonical_episodes,
            }
        )
    if frozenset(filenames_by_season) != host_contract.ALLOWED_SCHEMA_V1_SEASONS:
        raise TransferError("catalogue_mismatch")
    canonical_seasons.sort(key=lambda item: cast(int, item["season_number"]))
    canonical = {
        "schema_version": 1,
        "series": [
            {
                "series_id": host_contract.CANONICAL_SERIES_ID,
                "series_name": host_contract.CANONICAL_SERIES_NAME,
                "seasons": canonical_seasons,
            }
        ],
    }
    canonical_bytes = json.dumps(
        canonical,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return CatalogueSnapshot(hashlib.sha256(canonical_bytes).hexdigest(), filenames_by_season)


def _active_catalogue() -> CatalogueSnapshot:
    current = host_contract.CURRENT_LINK
    releases = host_contract.RELEASES_ROOT.resolve(strict=True)
    try:
        current_metadata = current.lstat()
        release = current.resolve(strict=True)
        release.relative_to(releases)
        release_metadata = release.lstat()
    except (OSError, ValueError) as error:
        raise TransferError("host_not_ready") from error
    if (
        not stat.S_ISLNK(current_metadata.st_mode)
        or not _root_owned(current_metadata)
        or not stat.S_ISDIR(release_metadata.st_mode)
        or stat.S_ISLNK(release_metadata.st_mode)
        or not _root_owned(release_metadata)
        or stat.S_IMODE(release_metadata.st_mode) & 0o022
        or _RELEASE_NAME_RE.fullmatch(release.name) is None
    ):
        raise TransferError("host_not_ready")
    catalogue = release / "knowledge/catalogue.json"
    catalogue_bytes = _regular_root_file(
        catalogue,
        mode=0o644,
        max_bytes=host_contract.MAX_PUBLIC_CATALOGUE_BYTES,
    )
    return _parse_catalogue(catalogue_bytes)


def _expected_selection(snapshot: CatalogueSnapshot, purpose: str, season: int) -> set[str]:
    policy = DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION
    filenames = snapshot.reviewed_filenames_by_season[season]
    season_directory = "Modern_Family" + policy.season_directory_suffix.format(season_number=season)
    if purpose == policy.purpose_speaker_review:
        aligned = {
            (
                PurePosixPath(season_directory)
                / policy.aligned_directory_name
                / (
                    filename[: -len(policy.reviewed_subtitle_suffix)]
                    + policy.aligned_subtitle_suffix
                )
            ).as_posix()
            for filename in filenames
        }
        return {
            policy.script_pdf_filename_template.format(season=season),
            *aligned,
        }
    if purpose == policy.purpose_reviewed_ingestion:
        reviewed_root = PurePosixPath(season_directory) / policy.reviewed_directory_name
        return {
            *(str(reviewed_root / filename) for filename in filenames),
            str(reviewed_root / policy.review_ledger_filename),
        }
    raise TransferError("catalogue_mismatch")


def _validate_ledger(
    root: Path,
    files: tuple[BundleFile, ...],
    expected_filenames: tuple[str, ...],
    season: int,
) -> None:
    policy = DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION
    season_directory = "Modern_Family" + policy.season_directory_suffix.format(season_number=season)
    reviewed_root = PurePosixPath(season_directory) / policy.reviewed_directory_name
    ledger_locator = str(reviewed_root / policy.review_ledger_filename)
    descriptors = {item.path: item for item in files}
    ledger_descriptor = descriptors.get(ledger_locator)
    if ledger_descriptor is None:
        raise TransferError("catalogue_mismatch")
    ledger_bytes = _regular_root_file(
        root.joinpath(*PurePosixPath(ledger_locator).parts),
        mode=0o600,
        max_bytes=min(policy.max_file_bytes, ledger_descriptor.size),
    )
    decoded = _strict_json(ledger_bytes, error_code="catalogue_mismatch")
    if not isinstance(decoded, dict) or set(decoded) != _LEDGER_KEYS:
        raise TransferError("catalogue_mismatch")
    review_status = decoded["review_status"]
    reviewed_by = decoded["reviewed_by"]
    reviewed_at = decoded["reviewed_at"]
    records = decoded["records"]
    if (
        type(decoded["schema_version"]) is not int
        or decoded["schema_version"] != 1
        or not isinstance(review_status, str)
        or review_status not in _APPROVED_REVIEW_STATUSES
        or not isinstance(reviewed_by, str)
        or not reviewed_by
        or reviewed_by.strip() != reviewed_by
        or not isinstance(reviewed_at, str)
        or not isinstance(records, list)
        or len(records) != len(expected_filenames)
    ):
        raise TransferError("catalogue_mismatch")
    try:
        parsed_timestamp = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise TransferError("catalogue_mismatch") from error
    if parsed_timestamp.tzinfo is None:
        raise TransferError("catalogue_mismatch")

    observed: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != _LEDGER_RECORD_KEYS:
            raise TransferError("catalogue_mismatch")
        candidate = record["candidate_filename"]
        reviewed = record["reviewed_filename"]
        removed_cues = record["removed_cue_numbers"]
        if (
            not isinstance(candidate, str)
            or not isinstance(reviewed, str)
            or reviewed in observed
            or reviewed not in expected_filenames
            or not isinstance(record["candidate_sha256"], str)
            or _SHA256_RE.fullmatch(record["candidate_sha256"]) is None
            or not isinstance(record["reviewed_sha256"], str)
            or _SHA256_RE.fullmatch(record["reviewed_sha256"]) is None
            or type(record["promoted_question_mark_labels"]) is not int
            or record["promoted_question_mark_labels"] < 0
            or type(record["removed_redaction_lines"]) is not int
            or record["removed_redaction_lines"] < 0
            or not isinstance(removed_cues, list)
            or any(type(value) is not int for value in removed_cues)
        ):
            raise TransferError("catalogue_mismatch")
        try:
            if (
                candidate != PurePosixPath(candidate).name
                or reviewed != PurePosixPath(reviewed).name
                or "\\" in candidate
                or "\\" in reviewed
            ):
                raise BundleError("ledger filename is not portable")
            _validate_name(candidate)
            _validate_name(reviewed)
        except BundleError as error:
            raise TransferError("catalogue_mismatch") from error
        locator = str(reviewed_root / reviewed)
        descriptor = descriptors.get(locator)
        if descriptor is None:
            raise TransferError("catalogue_mismatch")
        content = _regular_root_file(
            root.joinpath(*PurePosixPath(locator).parts),
            mode=0o600,
            max_bytes=descriptor.size,
        )
        try:
            if content.decode("utf-8").encode("utf-8") != content:
                raise UnicodeError
        except UnicodeError as error:
            raise TransferError("catalogue_mismatch") from error
        if hashlib.sha256(content).hexdigest() != record["reviewed_sha256"]:
            raise TransferError("catalogue_mismatch")
        observed.add(reviewed)
    if observed != set(expected_filenames):
        raise TransferError("catalogue_mismatch")


def _validate_catalogue_selection(
    root: Path,
    manifest: Mapping[str, object],
    files: tuple[BundleFile, ...],
) -> CatalogueSnapshot:
    loaded = _active_catalogue()
    if manifest["source_catalogue_sha256"] != loaded.content_sha256:
        raise TransferError("catalogue_mismatch")
    season_number = manifest["season_number"]
    if (
        type(season_number) is not int
        or season_number not in host_contract.ALLOWED_SCHEMA_V1_SEASONS
    ):
        raise TransferError("catalogue_mismatch")
    purpose = manifest["purpose"]
    if not isinstance(purpose, str):
        raise TransferError("catalogue_mismatch")
    expected = _expected_selection(loaded, purpose, season_number)
    if {item.path for item in files} != expected or len(files) != len(expected):
        raise TransferError("catalogue_mismatch")
    if purpose == DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION.purpose_reviewed_ingestion:
        _validate_ledger(
            root,
            files,
            loaded.reviewed_filenames_by_season[season_number],
            season_number,
        )
    return loaded


def _receipt(
    header: TransferHeader, manifest: Mapping[str, object], manifest_bytes: bytes
) -> dict[str, object]:
    return {
        "archive_sha256": header.archive_sha256,
        "catalogue_sha256": manifest["source_catalogue_sha256"],
        "file_count": manifest["file_count"],
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "protocol": header.protocol,
        "purpose": manifest["purpose"],
        "schema_version": host_contract.INSTALL_RECEIPT_SCHEMA_VERSION,
        "season_number": manifest["season_number"],
        "total_bytes": manifest["total_bytes"],
    }


def _write_private_file(path: Path, content: bytes) -> None:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _expected_directories(file_paths: set[str]) -> set[str]:
    directories = {"."}
    for item in file_paths:
        current = PurePosixPath(item).parent
        while current != PurePosixPath("."):
            directories.add(current.as_posix())
            current = current.parent
    return directories


def _verify_object(
    root: Path,
    header: TransferHeader,
    manifest: Mapping[str, object],
    files: tuple[BundleFile, ...],
) -> None:
    _require_private_root(root)
    policy = DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION
    manifest_bytes = _regular_root_file(
        root / policy.manifest_filename,
        mode=0o600,
        max_bytes=policy.max_manifest_bytes,
    )
    try:
        installed_manifest = _decode_manifest(manifest_bytes)
    except BundleError as error:
        raise TransferError("object_integrity") from error
    if installed_manifest != manifest:
        raise TransferError("object_integrity")
    receipt_bytes = _regular_root_file(
        root / host_contract.INSTALL_RECEIPT_FILENAME,
        mode=0o600,
        max_bytes=host_contract.STATUS_MAX_BYTES,
    )
    expected_receipt = _receipt(header, manifest, manifest_bytes)
    if receipt_bytes != host_contract.canonical_json(expected_receipt):
        raise TransferError("object_integrity")

    expected_files = {
        policy.manifest_filename,
        host_contract.INSTALL_RECEIPT_FILENAME,
        *(item.path for item in files),
    }
    expected_directories = _expected_directories(expected_files)
    observed_files: set[str] = set()
    observed_directories = {"."}
    for directory, names, filenames in os.walk(root, followlinks=False):
        relative_directory = Path(directory).relative_to(root).as_posix()
        if relative_directory == ".":
            relative_directory = "."
        directory_metadata = Path(directory).lstat()
        if (
            not stat.S_ISDIR(directory_metadata.st_mode)
            or stat.S_ISLNK(directory_metadata.st_mode)
            or not _root_owned(directory_metadata)
            or not _mode_is(directory_metadata, 0o700)
        ):
            raise TransferError("object_integrity")
        for name in names:
            candidate = Path(directory) / name
            metadata = candidate.lstat()
            relative = candidate.relative_to(root).as_posix()
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise TransferError("object_integrity")
            observed_directories.add(relative)
        for name in filenames:
            candidate = Path(directory) / name
            observed_files.add(candidate.relative_to(root).as_posix())
    if observed_files != expected_files or observed_directories != expected_directories:
        raise TransferError("object_integrity")
    for item in files:
        content = _regular_root_file(
            root.joinpath(*PurePosixPath(item.path).parts),
            mode=0o600,
            max_bytes=item.size,
        )
        if len(content) != item.size or hashlib.sha256(content).hexdigest() != item.sha256:
            raise TransferError("object_integrity")


def _fsync_tree(root: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directories = [root, *(path for path in root.rglob("*") if path.is_dir())]
    for directory in reversed(directories):
        descriptor = os.open(directory, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _cleanup_transaction(path: Path | None, identity: tuple[int, int] | None) -> None:
    if path is None or identity is None:
        return
    try:
        metadata = path.lstat()
        if (
            path.parent == host_contract.TRANSACTIONS_ROOT
            and path.name.startswith(host_contract.TRANSACTION_PREFIX)
            and stat.S_ISDIR(metadata.st_mode)
            and not stat.S_ISLNK(metadata.st_mode)
            and (metadata.st_dev, metadata.st_ino) == identity
            and _root_owned(metadata)
        ):
            shutil.rmtree(path)
    except OSError:
        # A crash or cleanup failure remains root-private for explicit quarantine
        # recovery; never broaden deletion to an unchecked replacement path.
        return


def receive_private_corpus(stream: BinaryIO) -> dict[str, object]:
    """Execute one synchronous, bounded, append-only transfer transaction."""

    _require_host_hierarchy()
    header = read_transfer_header(stream)
    _require_capacity(header)
    transaction: Path | None = None
    transaction_identity: tuple[int, int] | None = None
    try:
        transaction = Path(
            tempfile.mkdtemp(
                prefix=host_contract.TRANSACTION_PREFIX,
                dir=host_contract.TRANSACTIONS_ROOT,
            )
        )
        transaction.chmod(0o700)
        transaction_metadata = transaction.lstat()
        transaction_identity = (transaction_metadata.st_dev, transaction_metadata.st_ino)
        archive_path = transaction / ".bundle.zip"
        _write_received_archive(stream, archive_path, header)
        try:
            manifest, files = verify_bundle(archive_path)
        except BundleError as error:
            raise TransferError("bundle_rejected") from error
        final = host_contract.OBJECTS_ROOT / (host_contract.OBJECT_PREFIX + header.archive_sha256)
        if os.path.lexists(final):
            _verify_object(final, header, manifest, files)
            _validate_catalogue_selection(final, manifest, files)
            return _status("already_present", manifest)

        extracted = transaction / "extracted"
        _require_capacity(header)
        try:
            stage_bundle(archive_path=archive_path, destination=extracted)
        except BundleError as error:
            raise TransferError("bundle_rejected") from error
        _validate_catalogue_selection(extracted, manifest, files)
        policy = DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION
        manifest_bytes = _regular_root_file(
            extracted / policy.manifest_filename,
            mode=0o600,
            max_bytes=policy.max_manifest_bytes,
        )
        receipt = host_contract.canonical_json(_receipt(header, manifest, manifest_bytes))
        _write_private_file(extracted / host_contract.INSTALL_RECEIPT_FILENAME, receipt)
        _verify_object(extracted, header, manifest, files)
        _fsync_tree(extracted)
        _require_capacity(header)
        try:
            _rename_no_replace(extracted, final)
        except OSError as error:
            if error.errno != errno.EEXIST:
                raise TransferError("publish_failed") from error
            _verify_object(final, header, manifest, files)
            _validate_catalogue_selection(final, manifest, files)
            return _status("already_present", manifest)
        _fsync_tree(final)
        if os.name != "nt":
            objects_descriptor = os.open(
                host_contract.OBJECTS_ROOT,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(objects_descriptor)
            finally:
                os.close(objects_descriptor)
        return _status("installed", manifest)
    finally:
        _cleanup_transaction(transaction, transaction_identity)


def _status(status: str, manifest: Mapping[str, object]) -> dict[str, object]:
    return {
        "file_count": manifest["file_count"],
        "purpose": manifest["purpose"],
        "season_number": manifest["season_number"],
        "status": status,
        "total_bytes": manifest["total_bytes"],
    }


def _terminate_receive(_signum: int, _frame: object) -> None:
    """Turn the wrapper deadline into normal transaction cleanup."""

    raise TransferError("receive_interrupted")


def main() -> int:
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    sigterm_installed = False
    try:
        if os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0:
            raise TransferError("host_not_ready")
        signal.signal(signal.SIGTERM, _terminate_receive)
        sigterm_installed = True
        result = receive_private_corpus(sys.stdin.buffer)
    except Exception:
        payload = host_contract.canonical_json({"error": "transfer_rejected", "status": "error"})
        sys.stderr.buffer.write(payload)
        return 2
    finally:
        if sigterm_installed:
            signal.signal(signal.SIGTERM, previous_sigterm)
    payload = host_contract.canonical_json(result)
    if len(payload) > host_contract.STATUS_MAX_BYTES:
        sys.stderr.buffer.write(
            host_contract.canonical_json({"error": "transfer_rejected", "status": "error"})
        )
        return 2
    sys.stdout.buffer.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
