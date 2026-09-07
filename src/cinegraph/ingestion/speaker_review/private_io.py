"""Private, physically confined filesystem primitives for speaker review.

The workflow deals in private source documents and provider request artifacts.
This module keeps path policy and filesystem checks in one place.  Callers must
use a canonical run directory returned by :func:`create_run_directory` or
:func:`canonical_run_directory`; artifact locators are always relative POSIX
locators, never arbitrary ``Path`` objects.
"""

from __future__ import annotations

import os
import re
import stat
import tempfile
import unicodedata
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Final

from cinegraph.common.error_messages import SpeakerReviewErrorMessages
from cinegraph.config.speaker_review_filesystem import (
    DEFAULT_SPEAKER_REVIEW_FILESYSTEM_CONFIGURATION,
    WINDOWS_INVALID_FILENAME_CHARACTERS,
    WINDOWS_RESERVED_BASENAMES,
    SpeakerReviewFilesystemConfiguration,
)

_REPARSE_POINT: Final[int] = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_RUN_ID_SUFFIX: Final[str] = r"[0-9a-f]{16}"


class SpeakerReviewFilesystemError(RuntimeError):
    """A path-free, stable filesystem failure for the review workflow."""


class SpeakerReviewArtifactConflictError(
    SpeakerReviewFilesystemError,
    FileExistsError,
):
    """An immutable private artifact already exists with different bytes."""


@dataclass(frozen=True, slots=True)
class PrivateFileSnapshot:
    """Bytes read from one stable regular file instance."""

    path: Path
    content: bytes
    sha256: str
    size: int


def canonical_corpus_root(
    path: str | Path,
    *,
    configuration: SpeakerReviewFilesystemConfiguration = (
        DEFAULT_SPEAKER_REVIEW_FILESYSTEM_CONFIGURATION
    ),
) -> Path:
    """Return an absolute physical corpus root, or fail closed."""

    del configuration  # Kept for a uniform policy-aware public API.
    try:
        lexical = Path(os.path.abspath(os.fspath(path)))
        resolved = lexical.resolve(strict=True)
        metadata = lexical.lstat()
        _require_directory(metadata)
        _require_no_reparse_or_symlink(metadata)
        _require_same_physical_path(lexical, resolved)
        _require_physical_ancestors(lexical)
        return resolved
    except SpeakerReviewFilesystemError:
        raise
    except (OSError, TypeError, ValueError):
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_CORPUS_PATH_INVALID
        ) from None


def canonical_relative_locator(
    value: str | Path,
    *,
    configuration: SpeakerReviewFilesystemConfiguration = (
        DEFAULT_SPEAKER_REVIEW_FILESYSTEM_CONFIGURATION
    ),
) -> PurePosixPath:
    """Validate and return a canonical relative POSIX locator."""

    try:
        raw = os.fspath(value)
        if not isinstance(raw, str):
            raise ValueError
        if (
            not raw
            or "\x00" in raw
            or "\\" in raw
            or raw != unicodedata.normalize("NFC", raw)
            or raw.startswith("/")
            or raw.startswith("//")
            or re.match(r"^[A-Za-z]:", raw)
            or len(raw.encode("utf-8")) > configuration.path_max_bytes
        ):
            raise ValueError
        parts = raw.split("/")
        if any(not part or part in {".", ".."} for part in parts):
            raise ValueError
        locator = PurePosixPath(raw)
        if locator.is_absolute() or locator.as_posix() != raw:
            raise ValueError
        for part in parts:
            if (
                len(part.encode("utf-8")) > configuration.name_max_bytes
                or part[-1] in {".", " "}
                or any(
                    character in WINDOWS_INVALID_FILENAME_CHARACTERS
                    for character in part
                )
                or part.split(".", 1)[0].upper() in WINDOWS_RESERVED_BASENAMES
                or any(ord(character) < 32 or ord(character) == 127 for character in part)
            ):
                raise ValueError
        return locator
    except (TypeError, UnicodeError, ValueError):
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_CORPUS_PATH_INVALID
        ) from None


def canonical_run_directory(
    corpus_root: str | Path,
    run_id: str,
    *,
    configuration: SpeakerReviewFilesystemConfiguration = (
        DEFAULT_SPEAKER_REVIEW_FILESYSTEM_CONFIGURATION
    ),
) -> Path:
    """Validate an existing ``<root>/<run-dir>/<run-id>`` directory."""

    root = canonical_corpus_root(corpus_root, configuration=configuration)
    run_directory_locator = _single_component_locator(
        configuration.run_directory_name, configuration
    )
    validated_run_id = validate_run_id(run_id, configuration=configuration)
    parent = _existing_directory(root / run_directory_locator.name)
    if parent.parent != root:
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_RUN_DIRECTORY_INVALID
        )
    candidate = parent / validated_run_id
    resolved = _existing_directory(candidate)
    if resolved.parent != parent:
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_RUN_DIRECTORY_INVALID
        )
    return resolved


def create_run_directory(
    corpus_root: str | Path,
    run_id: str,
    *,
    configuration: SpeakerReviewFilesystemConfiguration = (
        DEFAULT_SPEAKER_REVIEW_FILESYSTEM_CONFIGURATION
    ),
) -> Path:
    """Create and return one private physical run directory."""

    root = canonical_corpus_root(corpus_root, configuration=configuration)
    run_directory_locator = _single_component_locator(
        configuration.run_directory_name, configuration
    )
    validated_run_id = validate_run_id(run_id, configuration=configuration)
    parent = root / run_directory_locator.name
    try:
        if os.path.lexists(parent):
            parent = _existing_directory(parent)
        else:
            parent.mkdir(mode=configuration.private_directory_mode)
            parent = _existing_directory(parent)
        _harden_directory(parent, configuration)
        candidate = parent / validated_run_id
        if os.path.lexists(candidate):
            raise SpeakerReviewFilesystemError(
                SpeakerReviewErrorMessages.SPEAKER_REVIEW_RUN_DIRECTORY_INVALID
            )
        candidate.mkdir(mode=configuration.private_directory_mode)
        resolved = _existing_directory(candidate)
        _harden_directory(resolved, configuration)
        _sync_parent(parent)
        return resolved
    except SpeakerReviewFilesystemError:
        raise
    except OSError:
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_RUN_DIRECTORY_INVALID
        ) from None


def validate_run_id(
    run_id: str,
    *,
    configuration: SpeakerReviewFilesystemConfiguration = (
        DEFAULT_SPEAKER_REVIEW_FILESYSTEM_CONFIGURATION
    ),
) -> str:
    """Validate the deterministic speaker-review run identifier."""

    try:
        locator = canonical_relative_locator(run_id, configuration=configuration)
        if len(locator.parts) != 1:
            raise ValueError
        expected = rf"{re.escape(configuration.run_id_prefix)}{_RUN_ID_SUFFIX}"
        if re.fullmatch(expected, run_id) is None:
            raise ValueError
        return run_id
    except (SpeakerReviewFilesystemError, TypeError, ValueError):
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_RUN_DIRECTORY_INVALID
        ) from None


def resolve_relative_file(
    corpus_root: str | Path,
    locator: str | Path,
    *,
    configuration: SpeakerReviewFilesystemConfiguration = (
        DEFAULT_SPEAKER_REVIEW_FILESYSTEM_CONFIGURATION
    ),
) -> Path:
    """Resolve one existing, physical regular file beneath ``corpus_root``."""

    root = canonical_corpus_root(corpus_root, configuration=configuration)
    relative = canonical_relative_locator(locator, configuration=configuration)
    current = root
    try:
        for part in relative.parts:
            current = current / part
            metadata = current.lstat()
            _require_no_reparse_or_symlink(metadata)
            if current != root / relative and not stat.S_ISDIR(metadata.st_mode):
                raise SpeakerReviewFilesystemError(
                    SpeakerReviewErrorMessages.SPEAKER_REVIEW_SOURCE_FILE_INVALID
                )
        resolved = current.resolve(strict=True)
        resolved.relative_to(root)
        _require_same_physical_path(current, resolved)
        metadata = resolved.lstat()
        _require_regular_nonlinked(metadata)
        return resolved
    except SpeakerReviewFilesystemError:
        raise
    except (OSError, ValueError):
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_SOURCE_FILE_INVALID
        ) from None


def resolve_relative_directory(
    corpus_root: str | Path,
    locator: str | Path,
    *,
    configuration: SpeakerReviewFilesystemConfiguration = (
        DEFAULT_SPEAKER_REVIEW_FILESYSTEM_CONFIGURATION
    ),
) -> Path:
    """Resolve one existing physical directory beneath ``corpus_root``."""

    root = canonical_corpus_root(corpus_root, configuration=configuration)
    relative = canonical_relative_locator(locator, configuration=configuration)
    current = root
    try:
        for part in relative.parts:
            current = current / part
            metadata = current.lstat()
            _require_directory(metadata)
            _require_no_reparse_or_symlink(metadata)
        resolved = current.resolve(strict=True)
        resolved.relative_to(root)
        _require_same_physical_path(current, resolved)
        _require_physical_ancestors(current)
        return resolved
    except SpeakerReviewFilesystemError:
        raise
    except (OSError, ValueError):
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_CORPUS_PATH_INVALID
        ) from None


def stable_file_snapshot(
    path: str | Path,
    *,
    max_bytes: int,
) -> PrivateFileSnapshot:
    """Read bounded bytes while proving one regular, non-hardlinked file instance."""

    descriptor = -1
    try:
        if not isinstance(max_bytes, int) or max_bytes < 0:
            raise ValueError
        source = Path(os.path.abspath(os.fspath(path)))
        _require_physical_ancestors(source.parent)
        before = source.lstat()
        _require_regular_nonlinked(before)
        if before.st_size > max_bytes:
            raise ValueError
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(source, flags)
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            opened = os.fstat(stream.fileno())
            if _identity(before) != _identity(opened):
                raise ValueError
            content = stream.read(max_bytes + 1)
        after = source.lstat()
        _require_regular_nonlinked(after)
        if (
            _identity(opened) != _identity(after)
            or len(content) != opened.st_size
            or len(content) > max_bytes
        ):
            raise ValueError
        return PrivateFileSnapshot(
            path=source,
            content=content,
            sha256=sha256(content).hexdigest(),
            size=len(content),
        )
    except SpeakerReviewFilesystemError:
        raise
    except (OSError, TypeError, ValueError):
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_SOURCE_FILE_INVALID
        ) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def stable_relative_file_snapshot(
    corpus_root: str | Path,
    locator: str | Path,
    *,
    max_bytes: int,
    configuration: SpeakerReviewFilesystemConfiguration = (
        DEFAULT_SPEAKER_REVIEW_FILESYSTEM_CONFIGURATION
    ),
) -> PrivateFileSnapshot:
    """Resolve and stably snapshot one root-relative private source file."""

    return stable_file_snapshot(
        resolve_relative_file(corpus_root, locator, configuration=configuration),
        max_bytes=max_bytes,
    )


def private_artifact_path(
    run_directory: str | Path,
    locator: str | Path,
    *,
    create_parents: bool = False,
    configuration: SpeakerReviewFilesystemConfiguration = (
        DEFAULT_SPEAKER_REVIEW_FILESYSTEM_CONFIGURATION
    ),
) -> Path:
    """Return a confined run-artifact path for a relative POSIX locator."""

    run = _existing_directory(Path(run_directory))
    _harden_directory(run, configuration)
    relative = canonical_relative_locator(locator, configuration=configuration)
    current = run
    try:
        for part in relative.parts[:-1]:
            current = current / part
            if not os.path.lexists(current):
                if not create_parents:
                    raise FileNotFoundError
                current.mkdir(mode=configuration.private_directory_mode)
            current = _existing_directory(current)
            _harden_directory(current, configuration)
        return current / relative.parts[-1]
    except SpeakerReviewFilesystemError:
        raise
    except OSError:
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_ARTIFACT_INVALID
        ) from None


def read_private_artifact(
    run_directory: str | Path,
    locator: str | Path,
    *,
    max_bytes: int,
    configuration: SpeakerReviewFilesystemConfiguration = (
        DEFAULT_SPEAKER_REVIEW_FILESYSTEM_CONFIGURATION
    ),
) -> bytes:
    """Read one bounded, stable private run artifact."""

    path = private_artifact_path(run_directory, locator, configuration=configuration)
    try:
        return stable_file_snapshot(path, max_bytes=max_bytes).content
    except SpeakerReviewFilesystemError as error:
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_ARTIFACT_INVALID
        ) from error


def write_private_file_once(
    run_directory: str | Path,
    locator: str | Path,
    content: bytes,
    *,
    configuration: SpeakerReviewFilesystemConfiguration = (
        DEFAULT_SPEAKER_REVIEW_FILESYSTEM_CONFIGURATION
    ),
) -> None:
    """Create one private artifact, permitting only an identical retry."""

    _validate_content(content, configuration.artifact_max_bytes)
    path = private_artifact_path(
        run_directory,
        locator,
        create_parents=True,
        configuration=configuration,
    )
    existing = _existing_leaf(path)
    if existing is not None:
        snapshot = stable_file_snapshot(path, max_bytes=configuration.artifact_max_bytes)
        if snapshot.content != content:
            raise SpeakerReviewArtifactConflictError(
                SpeakerReviewErrorMessages.SPEAKER_REVIEW_ARTIFACT_CONFLICT
            )
        _harden_file(path, configuration)
        return
    try:
        descriptor = _open_new_private_file(path, configuration)
        identity = os.fstat(descriptor)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            final = os.fstat(stream.fileno())
        _require_regular_nonlinked(final)
        if final.st_size != len(content) or final.st_ino != identity.st_ino:
            raise OSError
        _sync_parent(path.parent)
    except FileExistsError:
        snapshot = stable_file_snapshot(path, max_bytes=configuration.artifact_max_bytes)
        if snapshot.content != content:
                raise SpeakerReviewArtifactConflictError(
                    SpeakerReviewErrorMessages.SPEAKER_REVIEW_ARTIFACT_CONFLICT
                ) from None
    except SpeakerReviewFilesystemError:
        raise
    except OSError:
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_FILESYSTEM_IO_FAILED
        ) from None
    finally:
        if "descriptor" in locals() and descriptor >= 0:
            os.close(descriptor)


def replace_private_file(
    run_directory: str | Path,
    locator: str | Path,
    content: bytes,
    *,
    configuration: SpeakerReviewFilesystemConfiguration = (
        DEFAULT_SPEAKER_REVIEW_FILESYSTEM_CONFIGURATION
    ),
) -> None:
    """Atomically replace one safe private artifact with fsync discipline."""

    _validate_content(content, configuration.artifact_max_bytes)
    path = private_artifact_path(
        run_directory,
        locator,
        create_parents=True,
        configuration=configuration,
    )
    _existing_leaf(path)
    descriptor = -1
    temporary: Path | None = None
    temporary_identity: tuple[int, int] | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        temporary_identity = _owned_identity(os.fstat(descriptor))
        if os.name != "nt":
            os.chmod(temporary, configuration.private_file_mode)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            _require_regular_nonlinked(os.fstat(stream.fileno()))
        os.replace(temporary, path)
        temporary = None
        _sync_parent(path.parent)
    except SpeakerReviewFilesystemError:
        raise
    except OSError:
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_FILESYSTEM_IO_FAILED
        ) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None and temporary_identity is not None:
            _unlink_owned(temporary, temporary_identity)


def _single_component_locator(
    value: str,
    configuration: SpeakerReviewFilesystemConfiguration,
) -> PurePosixPath:
    locator = canonical_relative_locator(value, configuration=configuration)
    if len(locator.parts) != 1:
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_RUN_DIRECTORY_INVALID
        )
    return locator


def _existing_directory(path: Path) -> Path:
    try:
        lexical = Path(os.path.abspath(path))
        resolved = lexical.resolve(strict=True)
        metadata = lexical.lstat()
        _require_directory(metadata)
        _require_no_reparse_or_symlink(metadata)
        _require_same_physical_path(lexical, resolved)
        _require_physical_ancestors(lexical)
        return resolved
    except SpeakerReviewFilesystemError:
        raise
    except (OSError, ValueError):
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_RUN_DIRECTORY_INVALID
        ) from None


def _existing_leaf(path: Path) -> os.stat_result | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_ARTIFACT_INVALID
        ) from None
    try:
        _require_regular_nonlinked(metadata)
    except SpeakerReviewFilesystemError:
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_ARTIFACT_INVALID
        ) from None
    return metadata


def _open_new_private_file(
    path: Path,
    configuration: SpeakerReviewFilesystemConfiguration,
) -> int:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, configuration.private_file_mode)
    try:
        _require_regular_nonlinked(os.fstat(descriptor))
        return descriptor
    except SpeakerReviewFilesystemError:
        os.close(descriptor)
        raise


def _validate_content(content: bytes, max_bytes: int) -> None:
    if (
        not isinstance(content, bytes)
        or not isinstance(max_bytes, int)
        or max_bytes < 0
        or len(content) > max_bytes
    ):
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_ARTIFACT_INVALID
        )


def _require_directory(metadata: os.stat_result) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_CORPUS_PATH_INVALID
        )


def _require_regular_nonlinked(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
        or metadata.st_nlink != 1
    ):
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_SOURCE_FILE_INVALID
        )


def _require_no_reparse_or_symlink(metadata: os.stat_result) -> None:
    if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_CORPUS_PATH_INVALID
        )


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT)


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_nlink,
    )


def _owned_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _require_same_physical_path(lexical: Path, resolved: Path) -> None:
    if os.path.normcase(os.fspath(lexical)) != os.path.normcase(os.fspath(resolved)):
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_CORPUS_PATH_INVALID
        )


def _require_physical_ancestors(path: Path) -> None:
    current = path
    while True:
        metadata = current.lstat()
        _require_no_reparse_or_symlink(metadata)
        parent = current.parent
        if parent == current:
            return
        current = parent


def _harden_directory(
    path: Path,
    configuration: SpeakerReviewFilesystemConfiguration,
) -> None:
    if os.name != "nt":
        os.chmod(path, configuration.private_directory_mode)


def _harden_file(
    path: Path,
    configuration: SpeakerReviewFilesystemConfiguration,
) -> None:
    if os.name != "nt":
        os.chmod(path, configuration.private_file_mode)


def _sync_parent(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        os.fsync(descriptor)
    except OSError:
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_FILESYSTEM_IO_FAILED
        ) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _unlink_owned(path: Path, identity: tuple[int, int]) -> None:
    try:
        metadata = path.lstat()
        if _owned_identity(metadata) == identity and stat.S_ISREG(metadata.st_mode):
            path.unlink()
    except OSError:
        return
