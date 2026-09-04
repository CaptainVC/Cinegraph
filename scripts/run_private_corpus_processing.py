"""Root-only processor for one reviewed private-corpus object request."""

from __future__ import annotations

import concurrent.futures
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import IO, BinaryIO

_RELEASE_ROOT = Path(__file__).resolve().parents[1]
for _root in (_RELEASE_ROOT, _RELEASE_ROOT / "src"):
    if os.fspath(_root) not in sys.path:
        sys.path.insert(0, os.fspath(_root))

from cinegraph.common.private_corpus_bundle import (  # noqa: E402
    BundleFile,
    _decode_manifest,
    _rename_no_replace,
)
from cinegraph.common.private_corpus_policy import (  # noqa: E402
    DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION,
)
from scripts import private_corpus_host_contract as host_contract  # noqa: E402
from scripts import private_corpus_processing_contract as processing_contract  # noqa: E402
from scripts import receive_private_corpus as receiver  # noqa: E402

_RELEASE_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_IMAGE_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_ENV_IMAGE_BINDING_KEYS = frozenset(
    {
        "CINEGRAPH_ENVIRONMENT",
        "CINEGRAPH_IMAGE",
        "CINEGRAPH_IMAGE_DIGEST",
        "CINEGRAPH_RELEASE_SHA",
    }
)


class ProcessingError(RuntimeError):
    """A path-free processing rejection."""


def _read_request(stream: BinaryIO) -> dict[str, object]:
    raw = stream.readline(processing_contract.PROCESS_REQUEST_MAX_BYTES + 1)
    if stream.read(1):
        raise ProcessingError("invalid request")
    try:
        return processing_contract.parse_request(raw)
    except ValueError as error:
        raise ProcessingError("invalid request") from error


def _root_owned(result: os.stat_result) -> bool:
    return os.name == "nt" or (result.st_uid == 0 and result.st_gid == 0)


def _expected_owner(result: os.stat_result, uid: int, gid: int) -> bool:
    return os.name == "nt" or (result.st_uid == uid and result.st_gid == gid)


def _require_private_directory(path: Path, *, owner: tuple[int, int] | None = None) -> None:
    try:
        result = path.lstat()
    except OSError as error:
        raise ProcessingError("host not ready") from error
    if not stat.S_ISDIR(result.st_mode) or stat.S_ISLNK(result.st_mode):
        raise ProcessingError("host not ready")
    if owner is None:
        valid_owner = _root_owned(result)
    else:
        valid_owner = _expected_owner(result, *owner)
    if not valid_owner or stat.S_IMODE(result.st_mode) != 0o700:
        raise ProcessingError("host not ready")


def _run_readonly_git(arguments: list[str], cwd: Path) -> bytes:
    environment = os.environ.copy()
    environment.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
    try:
        result = subprocess.run(
            arguments,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ProcessingError("release is not ready") from error
    if result.returncode != 0:
        raise ProcessingError("release is not ready")
    return result.stdout


def _verify_release_tree(release: Path) -> None:
    """Require every release entry to remain a root-controlled physical path."""

    for directory, names, filenames in os.walk(release, followlinks=False):
        for name in (*names, *filenames):
            candidate = Path(directory) / name
            try:
                metadata = candidate.lstat()
            except OSError as error:
                raise ProcessingError("release is not ready") from error
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not _root_owned(metadata)
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                raise ProcessingError("release is not ready")


def _active_release() -> tuple[Path, receiver.CatalogueSnapshot]:
    # _active_catalogue performs the complete existing catalogue and current-link
    # checks.  Keep it as the single source of truth rather than duplicating or
    # weakening the receive boundary's validation.
    catalogue = receiver._active_catalogue()
    current = host_contract.CURRENT_LINK
    releases = host_contract.RELEASES_ROOT.resolve(strict=True)
    try:
        current_meta = current.lstat()
        release = current.resolve(strict=True)
        release.relative_to(releases)
        release_meta = release.lstat()
        git_meta = (release / ".git").lstat()
    except (OSError, ValueError) as error:
        raise ProcessingError("release is not ready") from error
    if (
        not stat.S_ISLNK(current_meta.st_mode)
        or not _root_owned(current_meta)
        or not stat.S_ISDIR(release_meta.st_mode)
        or stat.S_ISLNK(release_meta.st_mode)
        or not _root_owned(release_meta)
        or stat.S_IMODE(release_meta.st_mode) & 0o022
        or not stat.S_ISDIR(git_meta.st_mode)
        or stat.S_ISLNK(git_meta.st_mode)
        or not _root_owned(git_meta)
        or stat.S_IMODE(git_meta.st_mode) & 0o022
        or len(release.name) != 40
        or any(character not in "0123456789abcdef" for character in release.name)
    ):
        raise ProcessingError("release is not ready")
    _verify_release_tree(release)
    status = _run_readonly_git(
        ["git", "-C", os.fspath(release), "status", "--porcelain=v1", "--untracked-files=all"],
        release,
    )
    if status:
        raise ProcessingError("release is not ready")
    head = _run_readonly_git(
        ["git", "-C", os.fspath(release), "rev-parse", "--verify", "HEAD"], release
    ).strip()
    if head.decode("ascii", "ignore") != release.name:
        raise ProcessingError("release is not ready")
    main = _run_readonly_git(
        ["git", "-C", os.fspath(release), "rev-parse", "--verify", "refs/remotes/origin/main"],
        release,
    ).strip()
    if main != head:
        raise ProcessingError("release is not ready")
    return release, catalogue


def _release_image_reference(release: Path) -> str:
    """Bind the Compose image reference to the locked active release."""

    try:
        raw = receiver._regular_root_file(
            host_contract.DEV_ENV_FILE,
            mode=0o600,
            max_bytes=host_contract.DEV_ENV_MAX_BYTES,
        )
        values: dict[str, str] = {}
        for line in raw.decode("utf-8").splitlines():
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if separator and key in _ENV_IMAGE_BINDING_KEYS:
                if key in values:
                    raise ValueError("duplicate binding")
                values[key] = value
    except (UnicodeError, ValueError, receiver.TransferError) as error:
        raise ProcessingError("release image is not ready") from error
    release_sha = values.get("CINEGRAPH_RELEASE_SHA", "")
    environment = values.get("CINEGRAPH_ENVIRONMENT", "")
    image_name = values.get("CINEGRAPH_IMAGE", "")
    image_digest = values.get("CINEGRAPH_IMAGE_DIGEST", "")
    if (
        set(values) != _ENV_IMAGE_BINDING_KEYS
        or environment != host_contract.DEV_ENVIRONMENT_NAME
        or _RELEASE_SHA_PATTERN.fullmatch(release_sha) is None
        or release_sha != release.name
        or image_name != host_contract.CINEGRAPH_IMAGE_NAME
        or _IMAGE_DIGEST_PATTERN.fullmatch(image_digest) is None
    ):
        raise ProcessingError("release image is not ready")
    return f"{image_name}@{image_digest}"


def _verify_release_image(release: Path) -> None:
    reference = _release_image_reference(release)
    try:
        completed = subprocess.run(
            ["docker", "image", "inspect", reference, "--format", "{{json .Config.Labels}}"],
            cwd=release,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            check=False,
            timeout=10,
        )
        if (
            completed.returncode != 0
            or completed.stderr
            or len(completed.stdout) > processing_contract.PROCESS_OUTPUT_MAX_BYTES
        ):
            raise ValueError("image inspection failed")

        def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate label")
                result[key] = value
            return result

        labels = json.loads(
            completed.stdout.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (
        OSError,
        subprocess.SubprocessError,
        TimeoutError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
    ) as error:
        raise ProcessingError("release image is not ready") from error
    if not isinstance(labels, dict) or (
        labels.get(host_contract.CINEGRAPH_IMAGE_REVISION_LABEL) != release.name
        or labels.get(host_contract.CINEGRAPH_IMAGE_SOURCE_LABEL)
        != host_contract.CINEGRAPH_IMAGE_SOURCE
        or labels.get(host_contract.CINEGRAPH_IMAGE_VERSION_LABEL) != f"sha-{release.name}"
    ):
        raise ProcessingError("release image is not ready")


def _verified_object(
    digest: str, *, release: Path
) -> tuple[Path, dict[str, object], tuple[BundleFile, ...]]:
    del release  # the receiver's active-catalogue verifier already pinned release.
    object_root = host_contract.OBJECTS_ROOT / f"{host_contract.OBJECT_PREFIX}{digest}"
    policy = DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION
    try:
        manifest_raw = receiver._regular_root_file(
            object_root / policy.manifest_filename,
            mode=0o600,
            max_bytes=policy.max_manifest_bytes,
        )
        manifest = _decode_manifest(manifest_raw)
        files = tuple(
            BundleFile(item["path"], item["size"], item["sha256"]) for item in manifest["files"]
        )
        receipt_raw = receiver._regular_root_file(
            object_root / host_contract.INSTALL_RECEIPT_FILENAME,
            mode=0o600,
            max_bytes=host_contract.STATUS_MAX_BYTES,
        )

        def reject_receipt_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result

        receipt = json.loads(
            receipt_raw.decode("utf-8"),
            object_pairs_hook=reject_receipt_duplicates,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if not isinstance(receipt, dict) or receipt.get("archive_sha256") != digest:
            raise ProcessingError("private object rejected")
        header = receiver.TransferHeader(
            int(receipt.get("archive_bytes", 1)),
            digest,
            int(receipt.get("protocol", host_contract.TRANSFER_PROTOCOL_VERSION)),
        )
        receiver._verify_object(object_root, header, manifest, files)
        receiver._validate_catalogue_selection(object_root, manifest, files)
    except (KeyError, TypeError, ValueError, receiver.TransferError) as error:
        raise ProcessingError("private object rejected") from error
    if (
        manifest.get("purpose") != processing_contract.PROCESS_PURPOSE
        or manifest.get("season_number") != processing_contract.PROCESS_SEASON_NUMBER
    ):
        raise ProcessingError("private object rejected")
    return object_root, manifest, files


def _set_owner(path: Path, uid: int, gid: int) -> None:
    if os.name == "nt":
        return
    try:
        os.chown(path, uid, gid)
    except OSError as error:
        raise ProcessingError("processing workspace is unavailable") from error


def _make_workspace_directory(path: Path, *, owner: tuple[int, int]) -> None:
    try:
        path.mkdir(mode=0o700)
        path.chmod(0o700)
        _set_owner(path, *owner)
    except OSError as error:
        raise ProcessingError("processing workspace is unavailable") from error


def _copy_file(source: Path, destination: Path, expected: BundleFile | None = None) -> None:
    descriptor = -1
    source_descriptor = -1
    digest = hashlib.sha256()
    copied = 0
    try:
        before = source.lstat()
        if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or before.st_nlink != 1:
            raise ProcessingError("private object changed")
        source_descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(source_descriptor)
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with (
            os.fdopen(source_descriptor, "rb", closefd=True) as input_stream,
            os.fdopen(descriptor, "wb", closefd=True) as output_stream,
        ):
            source_descriptor = descriptor = -1
            while chunk := input_stream.read(1024 * 1024):
                copied += len(chunk)
                output_stream.write(chunk)
                digest.update(chunk)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        after = source.lstat()
        target = destination.lstat()
    except ProcessingError:
        raise
    except OSError as error:
        raise ProcessingError("processing workspace is unavailable") from error
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if descriptor >= 0:
            os.close(descriptor)
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_nlink)
        != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_nlink)
        or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_nlink)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_nlink)
        or copied != opened.st_size
        or target.st_nlink != 1
        or (
            expected is not None
            and (copied != expected.size or digest.hexdigest() != expected.sha256)
        )
    ):
        raise ProcessingError("private object changed")
    destination.chmod(0o600)
    _set_owner(destination, processing_contract.PROCESSING_UID, processing_contract.PROCESSING_GID)


def _workspace_files(manifest: dict[str, object], files: tuple[BundleFile, ...]) -> set[str]:
    policy = DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION
    return {
        policy.manifest_filename,
        host_contract.INSTALL_RECEIPT_FILENAME,
        *(item.path for item in files),
    }


def _verify_workspace(
    workspace: Path, manifest: dict[str, object], files: tuple[BundleFile, ...]
) -> None:
    expected_files = _workspace_files(manifest, files)
    expected_directories = {"."}
    for name in expected_files:
        current = PurePosixPath(name).parent
        while current != PurePosixPath("."):
            expected_directories.add(current.as_posix())
            current = current.parent
    try:
        root_meta = workspace.lstat()
    except OSError as error:
        raise ProcessingError("processing workspace is unavailable") from error
    if (
        not stat.S_ISDIR(root_meta.st_mode)
        or stat.S_ISLNK(root_meta.st_mode)
        or stat.S_IMODE(root_meta.st_mode) != 0o700
        or not _expected_owner(
            root_meta, processing_contract.PROCESSING_UID, processing_contract.PROCESSING_GID
        )
    ):
        raise ProcessingError("processing workspace is unavailable")
    observed_files: set[str] = set()
    observed_directories = {"."}
    for directory, names, filenames in os.walk(workspace, followlinks=False):
        relative_directory = Path(directory).relative_to(workspace).as_posix() or "."
        metadata = Path(directory).lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or not _expected_owner(
                metadata, processing_contract.PROCESSING_UID, processing_contract.PROCESSING_GID
            )
        ):
            raise ProcessingError("processing workspace is unavailable")
        observed_directories.add(relative_directory)
        for name in names:
            candidate = Path(directory) / name
            if not stat.S_ISDIR(candidate.lstat().st_mode) or candidate.is_symlink():
                raise ProcessingError("processing workspace is unavailable")
            observed_directories.add(candidate.relative_to(workspace).as_posix())
        for name in filenames:
            candidate = Path(directory) / name
            metadata = candidate.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
                or not _expected_owner(
                    metadata, processing_contract.PROCESSING_UID, processing_contract.PROCESSING_GID
                )
            ):
                raise ProcessingError("processing workspace is unavailable")
            observed_files.add(candidate.relative_to(workspace).as_posix())
    if observed_files != expected_files or observed_directories != expected_directories:
        raise ProcessingError("processing workspace is unavailable")
    descriptors = {item.path: item for item in files}
    policy = DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION
    for name in expected_files:
        if name in descriptors:
            expected = descriptors[name]
            content = (workspace / Path(*PurePosixPath(name).parts)).read_bytes()
            if (
                len(content) != expected.size
                or hashlib.sha256(content).hexdigest() != expected.sha256
            ):
                raise ProcessingError("processing workspace changed")
        elif name == policy.manifest_filename:
            content = (workspace / name).read_bytes()
            if _decode_manifest(content) != manifest:
                raise ProcessingError("processing workspace changed")


def _materialize(
    object_root: Path, manifest: dict[str, object], files: tuple[BundleFile, ...], digest: str
) -> Path:
    processing_root = processing_contract.PROCESSING_ROOT
    _require_private_directory(processing_root)
    final = processing_contract.workspace_for(digest)
    if os.path.lexists(final):
        _verify_workspace(final, manifest, files)
        return final
    stage: Path | None = None
    try:
        stage = Path(
            tempfile.mkdtemp(
                prefix=processing_contract.PROCESSING_STAGING_PREFIX, dir=processing_root
            )
        )
        stage.chmod(0o700)
        _set_owner(stage, processing_contract.PROCESSING_UID, processing_contract.PROCESSING_GID)
        expected = _workspace_files(manifest, files)
        for name in sorted(expected):
            destination = stage.joinpath(*PurePosixPath(name).parts)
            if not destination.parent.exists():
                current = stage
                for part in PurePosixPath(name).parts[:-1]:
                    current = current / part
                    if not current.exists():
                        _make_workspace_directory(
                            current,
                            owner=(
                                processing_contract.PROCESSING_UID,
                                processing_contract.PROCESSING_GID,
                            ),
                        )
            if name == DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION.manifest_filename:
                _copy_file(object_root / name, destination)
            elif name == host_contract.INSTALL_RECEIPT_FILENAME:
                _copy_file(object_root / name, destination)
            else:
                descriptor = next(item for item in files if item.path == name)
                _copy_file(
                    object_root.joinpath(*PurePosixPath(name).parts), destination, descriptor
                )
        _verify_workspace(stage, manifest, files)
        try:
            _rename_no_replace(stage, final)
            stage = None
        except OSError as error:
            if error.errno != errno.EEXIST:
                raise ProcessingError("processing workspace is unavailable") from error
            _verify_workspace(final, manifest, files)
        return final
    except ProcessingError:
        raise
    except (OSError, StopIteration) as error:
        raise ProcessingError("processing workspace is unavailable") from error
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)


def _ensure_receipts_root() -> None:
    root = processing_contract.PROCESSING_RECEIPTS_ROOT
    if os.path.lexists(root):
        _require_private_directory(root)
        return
    try:
        root.mkdir(mode=0o700)
        root.chmod(0o700)
        _set_owner(root, 0, 0)
    except OSError as error:
        raise ProcessingError("processing receipt store is unavailable") from error


def _read_bounded(stream: IO[bytes]) -> bytes:
    try:
        return stream.read(processing_contract.PROCESS_OUTPUT_MAX_BYTES + 1)
    finally:
        stream.close()


def _run_worker(release: Path, workspace: Path) -> dict[str, object]:
    compose = release / "deploy/compose.yaml"
    if not compose.is_file() or compose.is_symlink():
        raise ProcessingError("release is not ready")
    arguments = [
        "docker",
        "compose",
        "--env-file",
        os.fspath(host_contract.DEV_ENV_FILE),
        "--profile",
        "corpus-processing",
        "-f",
        os.fspath(compose),
        "run",
        "--rm",
        "--no-TTY",
        "--no-deps",
        "--pull",
        "never",
        "--user",
        f"{processing_contract.PROCESSING_UID}:{processing_contract.PROCESSING_GID}",
        "--volume",
        f"{workspace.as_posix()}:/private-corpus:ro",
        "corpus-reviewed-ingestion",
    ]
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            arguments,
            cwd=release,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        if process.stdout is None or process.stderr is None:
            raise ProcessingError("worker failed")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            out_future = executor.submit(_read_bounded, process.stdout)
            err_future = executor.submit(_read_bounded, process.stderr)
            returncode = process.wait(
                timeout=processing_contract.PROCESSING_WORKER_TIMEOUT_SECONDS
            )
            stdout = out_future.result(timeout=5)
            stderr = err_future.result(timeout=5)
        if (
            len(stdout) > processing_contract.PROCESS_OUTPUT_MAX_BYTES
            or len(stderr) > processing_contract.PROCESS_OUTPUT_MAX_BYTES
        ):
            raise ProcessingError("worker failed")
        if returncode != 0 or stderr:
            raise ProcessingError("worker failed")

        def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result

        decoded = json.loads(
            stdout.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if not isinstance(decoded, dict) or set(decoded) != processing_contract.WORKER_KEYS:
            raise ValueError
        for key in ("file_count", "total_bytes", "episode_count", "indexed_segment_count"):
            if type(decoded.get(key)) is not int or decoded[key] < 0:
                raise ValueError
        if (
            decoded.get("mode") != "ingest-reviewed"
            or decoded.get("purpose") != processing_contract.PROCESS_PURPOSE
            or decoded.get("season_number") != 1
            or decoded["file_count"] <= 0
            or decoded["total_bytes"] <= 0
            or decoded["episode_count"] > decoded["file_count"]
        ):
            raise ValueError
        if processing_contract.canonical_json(decoded) != stdout:
            raise ValueError
        return dict(decoded)
    except ProcessingError:
        raise
    except (
        OSError,
        subprocess.SubprocessError,
        TimeoutError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
    ) as error:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        raise ProcessingError("worker failed") from error


def _write_receipt(path: Path, payload: dict[str, object]) -> None:
    root = path.parent
    _require_private_directory(root)
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".receipt-", dir=root)
        temporary = Path(name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            output.write(processing_contract.canonical_json(payload))
            output.flush()
            os.fsync(output.fileno())
        _set_owner(temporary, 0, 0)
        _rename_no_replace(temporary, path)
        temporary = None
    except OSError as error:
        if error.errno == errno.EEXIST:
            raise ProcessingError("processing receipt already exists") from error
        raise ProcessingError("processing receipt could not be persisted") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _load_receipt(path: Path) -> dict[str, object]:
    try:
        raw = receiver._regular_root_file(
            path, mode=0o600, max_bytes=processing_contract.PROCESS_STATUS_MAX_BYTES
        )

        def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result

        decoded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if not isinstance(decoded, dict) or processing_contract.canonical_json(decoded) != raw:
            raise ValueError
        return decoded
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
        receiver.TransferError,
    ) as error:
        raise ProcessingError("processing receipt is invalid") from error


def _aggregate(
    *,
    status: str,
    mode: str,
    manifest: dict[str, object],
    indexed_segment_count: int,
    episode_count: int | None = None,
) -> dict[str, object]:
    subtitles = episode_count
    if subtitles is None:
        subtitles = sum(
            1
            for item in manifest["files"]
            if isinstance(item, dict) and str(item.get("path", "")).endswith(".reviewed.srt")
        )
    return {
        "episode_count": subtitles,
        "file_count": manifest["file_count"],
        "indexed_segment_count": indexed_segment_count,
        "mode": mode,
        "purpose": processing_contract.PROCESS_PURPOSE,
        "season_number": 1,
        "status": status,
        "total_bytes": manifest["total_bytes"],
    }


def process_request(request: dict[str, object]) -> dict[str, object]:
    try:
        request = processing_contract.parse_request(processing_contract.canonical_json(request))
    except (TypeError, ValueError) as error:
        raise ProcessingError("invalid request") from error
    digest = str(request["archive_sha256"])
    operation = str(request["operation"])
    release, _ = _active_release()
    _verify_release_image(release)
    object_root, manifest, files = _verified_object(digest, release=release)
    if operation == "validate":
        return _aggregate(
            status="validated", mode="validate", manifest=manifest, indexed_segment_count=0
        )

    _ensure_receipts_root()
    receipt_path = processing_contract.receipt_for(digest)
    workspace = _materialize(object_root, manifest, files, digest)
    # Re-verify the source after copying and before mounting the workspace read-only.
    _verified_object(digest, release=release)
    _verify_workspace(workspace, manifest, files)
    if os.path.lexists(receipt_path):
        receipt = _load_receipt(receipt_path)
        expected = {
            "archive_sha256": digest,
            "operation": operation,
            "result": receipt.get("result"),
            "schema_version": processing_contract.PROCESSING_RECEIPT_SCHEMA_VERSION,
        }
        if receipt != expected:
            raise ProcessingError("processing receipt is invalid")
        result = receipt["result"]
        if not isinstance(result, dict):
            raise ProcessingError("processing receipt is invalid")
        processing_contract.validate_aggregate(result, mode=operation, status="applied")
        return {**result, "status": "already_applied"}
    worker = _run_worker(release, workspace)
    if (
        worker["file_count"] != manifest["file_count"]
        or worker["total_bytes"] != manifest["total_bytes"]
    ):
        raise ProcessingError("worker result is invalid")
    expected_episodes = _aggregate(
        status="applied", mode=operation, manifest=manifest, indexed_segment_count=0
    )["episode_count"]
    if worker["episode_count"] != expected_episodes:
        raise ProcessingError("worker result is invalid")
    aggregate = _aggregate(
        status="applied",
        mode=operation,
        manifest=manifest,
        indexed_segment_count=int(worker["indexed_segment_count"]),
        episode_count=int(worker["episode_count"]),
    )
    receipt = {
        "archive_sha256": digest,
        "operation": operation,
        "result": aggregate,
        "schema_version": processing_contract.PROCESSING_RECEIPT_SCHEMA_VERSION,
    }
    _write_receipt(receipt_path, receipt)
    return aggregate


def _error_payload() -> bytes:
    return processing_contract.canonical_json({"error": "processing_rejected", "status": "error"})


def main() -> int:
    try:
        if os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0:
            raise ProcessingError("not root")
        if os.environ.get("SUDO_USER") != host_contract.CORPUS_USER:
            raise ProcessingError("invalid caller")
        request = _read_request(sys.stdin.buffer)
        result = process_request(request)
        processing_contract.validate_aggregate(
            result, mode=str(request["operation"]), status=str(result["status"])
        )
        payload = processing_contract.canonical_json(result)
        if len(payload) > processing_contract.PROCESS_STATUS_MAX_BYTES:
            raise ProcessingError("result is too large")
        sys.stdout.buffer.write(payload)
        return 0
    except Exception:
        sys.stderr.buffer.write(_error_payload())
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
