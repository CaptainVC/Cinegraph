"""Deterministic, fail-closed contracts for private-corpus handoff bundles.

The caller supplies an exact catalogue-derived file list. This module never
discovers corpus files and never includes the public catalogue; only its digest
is bound into the versioned manifest.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from cinegraph.common import private_corpus_policy as _bundle_config
from cinegraph.common.private_corpus_policy import PrivateCorpusBundleConfiguration

_DEFAULT_POLICY = _bundle_config.DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION
BUNDLE_SCHEMA_VERSION = _DEFAULT_POLICY.schema_version
MANIFEST_FILENAME = _DEFAULT_POLICY.manifest_filename
PURPOSE_REVIEWED_INGESTION = _DEFAULT_POLICY.purpose_reviewed_ingestion
PURPOSE_SPEAKER_REVIEW = _DEFAULT_POLICY.purpose_speaker_review
ALLOWED_PURPOSES = _DEFAULT_POLICY.allowed_purposes

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:")
_WINDOWS_RESERVED = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)
_WINDOWS_FORBIDDEN = frozenset('<>:"|?*')
_ZIP_ALLOWED_FLAGS = 0x800
_EOCD_SIZE = 22
_AT_FDCWD = -100
_RENAME_NOREPLACE = 1


class BundleError(ValueError):
    """A deliberately path-free bundle validation or staging failure."""


@dataclass(frozen=True, slots=True)
class BundleFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class BundleResult:
    purpose: str
    file_count: int
    total_bytes: int
    archive_bytes: int
    catalogue_sha256: str
    season_number: int


@dataclass(frozen=True, slots=True)
class _PreparedFile:
    descriptor: BundleFile
    content: bytes


def _policy() -> PrivateCorpusBundleConfiguration:
    return _bundle_config.DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _validate_catalogue_digest(value: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value) or value != value.lower():
        raise BundleError("catalogue digest is invalid")
    return value


def _is_reparse(result: os.stat_result) -> bool:
    return bool(getattr(result, "st_file_attributes", 0) & 0x400)


def _validate_name(name: str) -> None:
    policy = _policy()
    folded = name.casefold()
    if (
        not name
        or name != unicodedata.normalize("NFC", name)
        or len(name.encode("utf-8")) > policy.max_name_bytes
        or name[-1] in {" ", "."}
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
        or any(character in _WINDOWS_FORBIDDEN for character in name)
        or _WINDOWS_RESERVED.match(name)
    ):
        raise BundleError("bundle member name is not portable")
    if (
        folded in policy.forbidden_exact_names
        or re.search(policy.forbidden_name_pattern, name, re.IGNORECASE)
        or re.search(policy.private_key_pattern, name, re.IGNORECASE)
    ):
        raise BundleError("forbidden source name")


def _validate_relative_text(value: object, message: str = "bundle path is invalid") -> str:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        raise BundleError(message)
    if _DRIVE_PATH_RE.match(value) or value.startswith("//"):
        raise BundleError(message)
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise BundleError(message)
    if value != PurePosixPath(value).as_posix() or len(value.encode("utf-8")) > _policy().max_path_bytes:
        raise BundleError(message)
    for part in parts:
        _validate_name(part)
    return value


def _selected_text(candidate: str | Path) -> str:
    if isinstance(candidate, Path):
        if candidate.is_absolute():
            raise BundleError("selected source path must be relative")
        return candidate.as_posix()
    if not isinstance(candidate, str):
        raise BundleError("selected source path must be text")
    return candidate


def _stat_identity(result: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        result.st_size,
        result.st_mtime_ns,
        result.st_nlink,
    )


def _stable_file_bytes(path: Path) -> bytes:
    policy = _policy()
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or _is_reparse(before)
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > policy.max_file_bytes
        ):
            raise BundleError("source must be a nonempty regular non-hardlinked file")
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            content = handle.read(policy.max_file_bytes + 1)
        after = path.lstat()
    except BundleError:
        raise
    except OSError as error:
        raise BundleError("source file cannot be read") from error
    if (
        _stat_identity(before) != _stat_identity(opened)
        or _stat_identity(opened) != _stat_identity(after)
        or len(content) != opened.st_size
    ):
        raise BundleError("source file changed while the bundle was built")
    upper_content = content.upper()
    if any(marker in upper_content for marker in policy.forbidden_content_markers):
        raise BundleError("source content violates the private bundle policy")
    return content


def _prepare_source(root: Path, candidate: str | Path, seen: set[str]) -> _PreparedFile:
    relative = _validate_relative_text(
        _selected_text(candidate), "selected source path is not canonical"
    )
    folded = relative.casefold()
    if folded in seen:
        raise BundleError("duplicate or case-colliding source path")
    seen.add(folded)
    lexical = root.joinpath(*relative.split("/"))
    current = root
    try:
        for part in relative.split("/")[:-1]:
            current = current / part
            parent_stat = current.lstat()
            if stat.S_ISLNK(parent_stat.st_mode) or _is_reparse(parent_stat):
                raise BundleError("symlink or reparse-point source is not permitted")
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(root)
    except BundleError:
        raise
    except (OSError, ValueError) as error:
        raise BundleError("selected source is outside the knowledge root") from error
    content = _stable_file_bytes(resolved)
    descriptor = BundleFile(relative, len(content), hashlib.sha256(content).hexdigest())
    return _PreparedFile(descriptor, content)


def _validate_extension(path: str, purpose: str) -> None:
    policy = _policy()
    suffix = PurePosixPath(path).suffix.casefold()
    name = PurePosixPath(path).name.casefold()
    if suffix not in policy.allowed_extensions:
        raise BundleError("unexpected source extension")
    if purpose == policy.purpose_reviewed_ingestion:
        if name != policy.review_ledger_filename and not name.endswith(
            policy.reviewed_subtitle_suffix
        ):
            raise BundleError("reviewed-ingestion selection contains an unexpected file")
    elif suffix != ".pdf" and not name.endswith(policy.aligned_subtitle_suffix):
        raise BundleError("speaker-review selection contains an unexpected file")


def _validate_purpose_shape(
    files: Iterable[BundleFile], purpose: str, season_number: int
) -> None:
    policy = _policy()
    paths = tuple(PurePosixPath(item.path) for item in files)
    for path in paths:
        _validate_extension(path.as_posix(), purpose)
    if purpose == policy.purpose_reviewed_ingestion:
        ledgers = [path for path in paths if path.name.casefold() == policy.review_ledger_filename]
        subtitles = [
            path for path in paths if path.name.casefold().endswith(policy.reviewed_subtitle_suffix)
        ]
        if (
            len(ledgers) != 1
            or not subtitles
            or ledgers[0].parent.name.casefold() != policy.reviewed_directory_name.casefold()
            or not ledgers[0].parent.parent.name.casefold().endswith(
                policy.season_directory_suffix.format(
                    season_number=season_number
                ).casefold()
            )
            or any(path.parent != ledgers[0].parent for path in subtitles)
        ):
            raise BundleError("reviewed-ingestion bundle layout is invalid")
        return
    scripts = [path for path in paths if path.suffix.casefold() == ".pdf"]
    subtitles = [
        path for path in paths if path.name.casefold().endswith(policy.aligned_subtitle_suffix)
    ]
    if (
        len(scripts) != 1
        or not subtitles
        or scripts[0].name
        != policy.script_pdf_filename_template.format(season=season_number)
        or not subtitles[0].parent.parent.name.casefold().endswith(
            policy.season_directory_suffix.format(season_number=season_number).casefold()
        )
        or len({path.parent for path in subtitles}) != 1
        or subtitles[0].parent.name.casefold() != policy.aligned_directory_name.casefold()
        or scripts[0].parent != PurePosixPath(".")
    ):
        raise BundleError("speaker-review bundle layout is invalid")


def _manifest(
    purpose: str, season_number: int, files: tuple[BundleFile, ...], catalogue_sha256: str
) -> dict[str, Any]:
    return {
        "schema_version": _policy().schema_version,
        "purpose": purpose,
        "season_number": season_number,
        "files": [
            {"path": item.path, "size": item.size, "sha256": item.sha256} for item in files
        ],
        "file_count": len(files),
        "total_bytes": sum(item.size for item in files),
        "source_catalogue_sha256": catalogue_sha256,
    }


def _git_safe_output(path: Path) -> None:
    """Allow repository output only when Git proves it is ignored and untracked."""
    repository = next(
        (parent for parent in (path.parent, *path.parents) if (parent / ".git").exists()), None
    )
    if repository is None:
        return
    try:
        relative = path.relative_to(repository).as_posix()
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", relative],
            cwd=repository,
            capture_output=True,
            timeout=3,
        )
        ignored = subprocess.run(
            ["git", "check-ignore", "--quiet", "--", relative],
            cwd=repository,
            capture_output=True,
            timeout=3,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise BundleError("output repository safety could not be verified") from error
    if tracked.returncode not in {0, 1} or ignored.returncode not in {0, 1}:
        raise BundleError("output repository safety could not be verified")
    if tracked.returncode == 0 or ignored.returncode != 0:
        raise BundleError("output location is not an ignored private staging location")


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.compress_type = _policy().archive_compression
    info.external_attr = _policy().archive_mode << 16
    return info


def _owned_file_identity(result: os.stat_result) -> tuple[int, int]:
    return result.st_dev, result.st_ino


def _unlink_owned_output(path: Path, identity: tuple[int, int] | None) -> None:
    if identity is None:
        return
    try:
        current = path.lstat()
        if (
            stat.S_ISREG(current.st_mode)
            and not stat.S_ISLNK(current.st_mode)
            and _owned_file_identity(current) == identity
        ):
            path.unlink()
    except OSError:
        pass


def _validate_root(path: Path) -> Path:
    try:
        result = path.lstat()
        if not stat.S_ISDIR(result.st_mode) or stat.S_ISLNK(result.st_mode) or _is_reparse(result):
            raise BundleError("knowledge root must be a physical directory")
        return path.resolve(strict=True)
    except BundleError:
        raise
    except OSError as error:
        raise BundleError("knowledge root must be a physical directory") from error


def build_bundle(
    *,
    source_root: Path,
    output_archive: Path,
    purpose: str,
    selected_paths: Iterable[str | Path],
    catalogue_sha256: str,
    season_number: int,
    dry_run: bool = False,
) -> BundleResult:
    """Build a deterministic archive from an exact list of source-relative files."""
    policy = _policy()
    if purpose not in policy.allowed_purposes:
        raise BundleError("unsupported bundle purpose")
    if not isinstance(season_number, int) or isinstance(season_number, bool) or season_number < 1:
        raise BundleError("season number must be a positive integer")
    catalogue_digest = _validate_catalogue_digest(catalogue_sha256)
    root = _validate_root(Path(source_root))
    output = Path(output_archive)
    if os.path.lexists(output):
        raise BundleError("output archive already exists")
    try:
        parent_stat = output.parent.lstat()
        if (
            not stat.S_ISDIR(parent_stat.st_mode)
            or stat.S_ISLNK(parent_stat.st_mode)
            or _is_reparse(parent_stat)
        ):
            raise BundleError("output parent directory is invalid")
        output.resolve().relative_to(root)
    except ValueError:
        pass
    except BundleError:
        raise
    except OSError as error:
        raise BundleError("output parent directory is invalid") from error
    else:
        raise BundleError("output archive cannot be inside the knowledge root")
    if output.suffix.casefold() != ".zip":
        raise BundleError("output archive must use the .zip extension")
    _git_safe_output(output.resolve())
    selected = tuple(selected_paths)
    if not selected or len(selected) > policy.max_file_count:
        raise BundleError("bundle file count exceeds the configured limit")
    seen: set[str] = set()
    prepared = tuple(
        sorted(
            (_prepare_source(root, item, seen) for item in selected),
            key=lambda item: item.descriptor.path.encode("utf-8"),
        )
    )
    files = tuple(item.descriptor for item in prepared)
    total_bytes = sum(item.size for item in files)
    if total_bytes > policy.max_total_bytes:
        raise BundleError("bundle size exceeds the configured limit")
    _validate_purpose_shape(files, purpose, season_number)
    manifest_bytes = _canonical_json(
        _manifest(purpose, season_number, files, catalogue_digest)
    )
    if len(manifest_bytes) > policy.max_manifest_bytes:
        raise BundleError("bundle manifest exceeds the configured limit")
    if dry_run:
        return BundleResult(purpose, len(files), total_bytes, 0, catalogue_digest, season_number)

    descriptor: int | None = None
    temporary: Path | None = None
    temporary_identity: tuple[int, int] | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
        )
        temporary = Path(temporary_name)
        temporary_identity = _owned_file_identity(os.fstat(descriptor))
        if os.name != "nt":
            os.chmod(temporary, policy.file_mode)
        _git_safe_output(temporary.resolve())
        with os.fdopen(descriptor, "w+b") as handle:
            descriptor = None
            with zipfile.ZipFile(handle, "w", compression=policy.archive_compression) as archive:
                archive.writestr(_zip_info(policy.manifest_filename), manifest_bytes)
                for item in prepared:
                    archive.writestr(_zip_info(item.descriptor.path), item.content)
            handle.flush()
            os.fsync(handle.fileno())
        archive_bytes = temporary.stat().st_size
        if archive_bytes > policy.max_archive_bytes:
            raise BundleError("bundle archive exceeds the configured limit")
        verify_bundle(temporary)
        _rename_no_replace(temporary, output)
        temporary = None
    except BundleError:
        raise
    except (OSError, zipfile.BadZipFile) as error:
        raise BundleError("bundle archive could not be written") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            _unlink_owned_output(temporary, temporary_identity)
    return BundleResult(purpose, len(files), total_bytes, archive_bytes, catalogue_digest, season_number)


def _decode_manifest(raw: bytes) -> dict[str, Any]:
    policy = _policy()
    if (
        not raw
        or len(raw) > policy.max_manifest_bytes
        or raw.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"))
    ):
        raise BundleError("bundle manifest is malformed")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        decoded: dict[str, Any] = {}
        for key, value in pairs:
            if key in decoded:
                raise BundleError("bundle manifest is malformed")
            decoded[key] = value
        return decoded

    try:
        decoded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, BundleError) as error:
        raise BundleError("bundle manifest is malformed") from error
    if not isinstance(decoded, dict) or _canonical_json(decoded) != raw:
        raise BundleError("bundle manifest is not canonical")
    required = {
        "schema_version",
        "purpose",
        "season_number",
        "files",
        "file_count",
        "total_bytes",
        "source_catalogue_sha256",
    }
    if set(decoded) != required:
        raise BundleError("bundle manifest schema is invalid")
    if type(decoded["schema_version"]) is not int or decoded["schema_version"] != policy.schema_version:
        raise BundleError("unsupported bundle schema version")
    purpose = decoded["purpose"]
    if not isinstance(purpose, str) or purpose not in policy.allowed_purposes:
        raise BundleError("unsupported bundle purpose")
    season = decoded["season_number"]
    if type(season) is not int or season < 1:
        raise BundleError("bundle season number is invalid")
    raw_files = decoded["files"]
    if not isinstance(raw_files, list) or not raw_files or len(raw_files) > policy.max_file_count:
        raise BundleError("bundle file count exceeds the configured limit")
    files: list[BundleFile] = []
    previous = b""
    folded: set[str] = set()
    for entry in raw_files:
        if not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256"}:
            raise BundleError("bundle manifest file entry is invalid")
        path = _validate_relative_text(entry["path"], "bundle manifest path is invalid")
        encoded = path.encode("utf-8")
        if encoded <= previous or path.casefold() in folded:
            raise BundleError("bundle manifest paths are not canonical")
        previous = encoded
        folded.add(path.casefold())
        size = entry["size"]
        digest = entry["sha256"]
        if type(size) is not int or size <= 0 or size > policy.max_file_bytes:
            raise BundleError("bundle manifest size is invalid")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise BundleError("bundle manifest hash is invalid")
        files.append(BundleFile(path, size, digest))
    total = decoded["total_bytes"]
    count = decoded["file_count"]
    if (
        type(count) is not int
        or type(total) is not int
        or count != len(files)
        or total != sum(item.size for item in files)
        or total > policy.max_total_bytes
    ):
        raise BundleError("bundle manifest aggregate is invalid")
    _validate_catalogue_digest(decoded["source_catalogue_sha256"])
    _validate_purpose_shape(files, purpose, season)
    return decoded


def _regular_zip_member(info: zipfile.ZipInfo) -> bool:
    policy = _policy()
    mode = (info.external_attr >> 16) & 0xFFFF
    return (
        not info.is_dir()
        and info.create_system == 3
        and mode == policy.archive_mode
        and info.compress_type == policy.archive_compression
        and not (info.external_attr & 0x10)
        and not (info.flag_bits & ~_ZIP_ALLOWED_FLAGS)
        and not info.extra
        and not info.comment
    )


def _read_zip_end(handle: Any, archive_size: int) -> tuple[int, int, int]:
    if archive_size < _EOCD_SIZE:
        raise BundleError("bundle archive has trailing or ambiguous data")
    handle.seek(-_EOCD_SIZE, os.SEEK_END)
    raw = handle.read(_EOCD_SIZE)
    try:
        (
            signature,
            disk,
            start_disk,
            disk_count,
            total_count,
            central_size,
            central_offset,
            comment,
        ) = struct.unpack("<4s4H2IH", raw)
    except struct.error as error:
        raise BundleError("bundle archive has trailing or ambiguous data") from error
    if (
        signature != b"PK\x05\x06"
        or disk != 0
        or start_disk != 0
        or disk_count != total_count
        or total_count > _policy().max_file_count + 1
        or comment != 0
        or central_size == 0xFFFFFFFF
        or central_offset == 0xFFFFFFFF
        or central_offset + central_size != archive_size - _EOCD_SIZE
    ):
        raise BundleError("bundle archive has trailing or ambiguous data")
    return total_count, central_size, central_offset


def _check_zip_end(handle: Any, archive_size: int, archive: zipfile.ZipFile, count: int) -> None:
    expected_count, _, central_offset = _read_zip_end(handle, archive_size)
    if expected_count != count or archive.start_dir != central_offset or archive.comment:
        raise BundleError("bundle archive has trailing or ambiguous data")


def _verified_member_bytes(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo, item: BundleFile
) -> bytes:
    if not _regular_zip_member(info) or info.file_size != item.size:
        raise BundleError("bundle archive member metadata is invalid")
    content = archive.read(info)
    if len(content) != item.size or hashlib.sha256(content).hexdigest() != item.sha256:
        raise BundleError("bundle archive content hash does not match the manifest")
    if any(marker in content.upper() for marker in _policy().forbidden_content_markers):
        raise BundleError("bundle archive content violates the private bundle policy")
    return content


def verify_bundle(archive_path: Path) -> tuple[dict[str, Any], tuple[BundleFile, ...]]:
    """Treat archive bytes and metadata as hostile and verify without extraction."""
    path = Path(archive_path)
    policy = _policy()
    try:
        result = path.lstat()
        if (
            not stat.S_ISREG(result.st_mode)
            or stat.S_ISLNK(result.st_mode)
            or _is_reparse(result)
            or result.st_nlink != 1
            or result.st_size <= 0
            or result.st_size > policy.max_archive_bytes
        ):
            raise BundleError("bundle archive must be a bounded regular non-hardlinked file")
        with path.open("rb") as handle:
            _read_zip_end(handle, result.st_size)
            handle.seek(0)
            archive = zipfile.ZipFile(handle, "r")
            try:
                infos = archive.infolist()
                _check_zip_end(handle, result.st_size, archive, len(infos))
                if not infos or infos[0].header_offset != 0:
                    raise BundleError("bundle archive layout is invalid")
                names = [info.filename for info in infos]
                folded: set[str] = set()
                for name in names:
                    _validate_relative_text(name, "bundle archive has an invalid member")
                    if name.casefold() in folded:
                        raise BundleError("bundle archive has duplicate members")
                    folded.add(name.casefold())
                if names.count(policy.manifest_filename) != 1:
                    raise BundleError("bundle archive manifest is missing")
                manifest_info = infos[0]
                if (
                    manifest_info.filename != policy.manifest_filename
                    or not _regular_zip_member(manifest_info)
                ):
                    raise BundleError("bundle archive manifest member is invalid")
                if manifest_info.file_size > policy.max_manifest_bytes:
                    raise BundleError("bundle archive manifest exceeds the configured limit")
                manifest = _decode_manifest(archive.read(manifest_info))
                files = tuple(
                    BundleFile(item["path"], item["size"], item["sha256"])
                    for item in manifest["files"]
                )
                expected = [policy.manifest_filename, *(item.path for item in files)]
                if names != expected:
                    raise BundleError("bundle archive members do not match the manifest")
                for info, item in zip(infos[1:], files, strict=True):
                    _verified_member_bytes(archive, info, item)
                after = os.fstat(handle.fileno())
                if (result.st_dev, result.st_ino, result.st_size, result.st_mtime_ns) != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                ):
                    raise BundleError("bundle archive changed during verification")
                return manifest, files
            finally:
                archive.close()
    except BundleError:
        raise
    except (OSError, zipfile.BadZipFile, KeyError, RuntimeError, ValueError) as error:
        raise BundleError("bundle archive is malformed") from error


def _private_physical_directory(path: Path) -> Path:
    try:
        absolute = Path(os.path.abspath(path))
        resolved = path.resolve(strict=True)
        result = path.lstat()
        if (
            os.path.normcase(os.fspath(absolute)) != os.path.normcase(os.fspath(resolved))
            or not stat.S_ISDIR(result.st_mode)
            or stat.S_ISLNK(result.st_mode)
            or _is_reparse(result)
        ):
            raise BundleError("staging parent must be a physical directory")
        if os.name != "nt":
            effective_user_id = getattr(os, "geteuid", None)
            if (
                stat.S_IMODE(result.st_mode) & 0o077
                or effective_user_id is None
                or result.st_uid != effective_user_id()
            ):
                raise BundleError("staging parent must be private and owned by the current user")
        return resolved
    except BundleError:
        raise
    except OSError as error:
        raise BundleError("staging parent directory is invalid") from error


def _copy_archive_snapshot(source: Path, destination: Path) -> int:
    policy = _policy()
    descriptor: int | None = None
    try:
        before = source.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or _is_reparse(before)
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > policy.max_archive_bytes
        ):
            raise BundleError("bundle source must be a bounded regular non-hardlinked file")
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, policy.file_mode)
        with source.open("rb") as input_handle, os.fdopen(descriptor, "wb") as output_handle:
            descriptor = None
            opened = os.fstat(input_handle.fileno())
            copied = 0
            while chunk := input_handle.read(1024 * 1024):
                copied += len(chunk)
                if copied > policy.max_archive_bytes:
                    raise BundleError("bundle archive exceeds the configured limit")
                output_handle.write(chunk)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        after = source.lstat()
        if (
            _stat_identity(before) != _stat_identity(opened)
            or _stat_identity(opened) != _stat_identity(after)
            or copied != opened.st_size
        ):
            raise BundleError("bundle source changed while it was snapshotted")
        return copied
    except BundleError:
        raise
    except OSError as error:
        raise BundleError("bundle archive could not be snapshotted") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write_private_file(path: Path, content: bytes) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, _policy().file_mode)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _rename_no_replace(source: Path, destination: Path) -> None:
    if sys.platform.startswith("linux"):
        library = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(library, "renameat2", None)
        if renameat2 is None:
            raise OSError(errno.ENOSYS, "atomic no-replace rename is unavailable")
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        if (
            renameat2(
                _AT_FDCWD,
                os.fsencode(source),
                _AT_FDCWD,
                os.fsencode(destination),
                _RENAME_NOREPLACE,
            )
            != 0
        ):
            number = ctypes.get_errno()
            raise OSError(number, os.strerror(number))
        return
    os.rename(source, destination)


def _make_private_parents(root: Path, parent: Path) -> None:
    """Create and explicitly harden every bundle-owned parent below root."""

    parent.mkdir(parents=True, exist_ok=True, mode=_policy().directory_mode)
    if os.name == "nt":
        return
    current = parent
    while current != root:
        current.chmod(_policy().directory_mode)
        current = current.parent


def stage_bundle(*, archive_path: Path, destination: Path) -> BundleResult:
    """Snapshot, verify, and atomically publish into a fresh private staging path."""
    source = Path(archive_path)
    requested_destination = Path(destination)
    if not requested_destination.is_absolute() or not requested_destination.name:
        raise BundleError("staging destination must be an absolute canonical path")
    if os.path.lexists(requested_destination):
        raise BundleError("staging destination must not already exist")
    parent = _private_physical_directory(requested_destination.parent)
    final_destination = parent / requested_destination.name
    if final_destination != requested_destination:
        raise BundleError("staging destination must be canonical")
    lock = parent / f".{requested_destination.name}.private-corpus.lock"
    lock_descriptor: int | None = None
    lock_acquired = False
    temp: Path | None = None
    manifest: dict[str, Any] | None = None
    files: tuple[BundleFile, ...] = ()
    archive_bytes = 0
    try:
        lock_descriptor = os.open(
            lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, _policy().file_mode
        )
        lock_acquired = True
        temp = Path(tempfile.mkdtemp(prefix=".private-corpus-stage-", dir=parent))
        if os.name != "nt":
            temp.chmod(_policy().directory_mode)
        snapshot = temp / ".bundle.zip"
        archive_bytes = _copy_archive_snapshot(source, snapshot)
        manifest, files = verify_bundle(snapshot)
        _write_private_file(temp / _policy().manifest_filename, _canonical_json(manifest))
        with zipfile.ZipFile(snapshot, "r") as archive:
            for item in files:
                output = temp.joinpath(*PurePosixPath(item.path).parts)
                _make_private_parents(temp, output.parent)
                content = _verified_member_bytes(archive, archive.getinfo(item.path), item)
                _write_private_file(output, content)
        snapshot.unlink()
        if os.path.lexists(final_destination):
            raise BundleError("staging destination must not already exist")
        _rename_no_replace(temp, final_destination)
        temp = None
    except BundleError:
        raise
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        raise BundleError("bundle staging failed; destination was not published") from error
    finally:
        if temp is not None:
            shutil.rmtree(temp, ignore_errors=True)
        if lock_descriptor is not None:
            os.close(lock_descriptor)
        if lock_acquired:
            try:
                lock.unlink(missing_ok=True)
            except OSError:
                pass
    if manifest is None:
        raise BundleError("bundle staging failed; destination was not published")
    return BundleResult(
        purpose=manifest["purpose"],
        file_count=manifest["file_count"],
        total_bytes=manifest["total_bytes"],
        archive_bytes=archive_bytes,
        catalogue_sha256=manifest["source_catalogue_sha256"],
        season_number=manifest["season_number"],
    )


def build_reviewed_ingestion_bundle(
    *,
    source_root: Path,
    output_archive: Path,
    reviewed_srt_paths: Iterable[str | Path],
    review_ledger_path: str | Path,
    catalogue_sha256: str,
    season_number: int,
) -> BundleResult:
    return build_bundle(
        source_root=source_root,
        output_archive=output_archive,
        purpose=PURPOSE_REVIEWED_INGESTION,
        selected_paths=(*reviewed_srt_paths, review_ledger_path),
        catalogue_sha256=catalogue_sha256,
        season_number=season_number,
    )


def build_speaker_review_bundle(
    *,
    source_root: Path,
    output_archive: Path,
    script_pdf_path: str | Path,
    aligned_srt_paths: Iterable[str | Path],
    catalogue_sha256: str,
    season_number: int,
) -> BundleResult:
    return build_bundle(
        source_root=source_root,
        output_archive=output_archive,
        purpose=PURPOSE_SPEAKER_REVIEW,
        selected_paths=(script_pdf_path, *aligned_srt_paths),
        catalogue_sha256=catalogue_sha256,
        season_number=season_number,
    )
