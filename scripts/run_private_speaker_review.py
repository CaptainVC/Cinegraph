"""Root-only coordinator for offline Season 2 speaker-review preparation."""

from __future__ import annotations

import concurrent.futures
import errno
import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import IO, BinaryIO, Final

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
from scripts import private_speaker_review_contract as review_contract  # noqa: E402
from scripts import receive_private_corpus as receiver  # noqa: E402
from scripts import run_private_corpus_processing as host_runtime  # noqa: E402

_OBJECT_DIRECTORY_PREFIX: Final = "sha256-"
_STAGING_PREFIX: Final = ".prepare-"
_RECEIPT_PREFIX: Final = "sha256-"
_RECEIPT_SUFFIX: Final = ".json"
_RECEIPT_SCHEMA_VERSION: Final = 1
_SOURCE_DIRECTORY_MODE: Final = 0o750
_SOURCE_STAGING_FILE_MODE: Final = 0o600
_SOURCE_FILE_MODE: Final = 0o440
_RUN_PARENT_MODE: Final = 0o700
_RUN_DIRECTORY_NAME: Final = "review-runs"
_RUN_STATE_FILENAME: Final = "run-state.json"
_RUN_STATE_MAX_BYTES: Final = 64 * 1024
_CANDIDATES_FILENAME: Final = "candidates.jsonl"
_SOURCE_MANIFEST_FILENAME: Final = "source-manifest.json"
_PRIMARY_REQUEST_FILENAME_TEMPLATE: Final = "primary-part-{part_number:04d}-requests.jsonl"
_RUN_ARTIFACT_MAX_BYTES: Final = 64 * 1024 * 1024
_RUN_ARTIFACT_TOTAL_MAX_BYTES: Final = 256 * 1024 * 1024
_WORKER_SHUTDOWN_SECONDS: Final = 60
_WORKER_TIMEOUT_SECONDS: Final = (
    host_contract.SPEAKER_REVIEW_TIMEOUT_SECONDS - _WORKER_SHUTDOWN_SECONDS
)
_WORKER_TERMINATION_GRACE_SECONDS: Final = min(
    _WORKER_SHUTDOWN_SECONDS,
    host_contract.SPEAKER_REVIEW_KILL_AFTER_SECONDS,
)
_READ_CHUNK_BYTES: Final = 1024 * 1024
_CONFIGURATION_BINDING_FILES: Final = (
    "src/cinegraph/common/prompts.py",
    "src/cinegraph/config/models.py",
    "src/cinegraph/config/speaker_review.py",
    "src/cinegraph/common/speaker_review_cost_policy.py",
    "src/cinegraph/config/speaker_review_filesystem.py",
    "src/cinegraph/ingestion/speaker_review/batch_requests.py",
    "src/cinegraph/ingestion/speaker_review/costs.py",
)
_RECEIPT_KEYS: Final = frozenset(
    {
        "archive_sha256",
        "artifact_file_count",
        "artifact_set_sha256",
        "catalogue_sha256",
        "configuration_sha256",
        "image_reference",
        "release_sha",
        "result",
        "schema_version",
    }
)


class SpeakerReviewProcessingError(RuntimeError):
    """A deliberately path-free preparation rejection."""


def _read_request(stream: BinaryIO) -> dict[str, object]:
    raw = stream.readline(review_contract.REVIEW_REQUEST_MAX_BYTES + 1)
    if stream.read(1):
        raise SpeakerReviewProcessingError("invalid request")
    try:
        return review_contract.parse_request(raw)
    except ValueError as error:
        raise SpeakerReviewProcessingError("invalid request") from error


def _expected_owner(metadata: os.stat_result, uid: int, gid: int) -> bool:
    return os.name == "nt" or (metadata.st_uid == uid and metadata.st_gid == gid)


def _set_owner(path: Path, uid: int, gid: int) -> None:
    if os.name == "nt":
        return
    try:
        os.chown(path, uid, gid)
    except OSError as error:
        raise SpeakerReviewProcessingError("workspace unavailable") from error


def _require_root_private_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise SpeakerReviewProcessingError("host not ready") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or not _expected_owner(metadata, 0, 0)
    ):
        raise SpeakerReviewProcessingError("host not ready")


def _verified_object(
    digest: str,
) -> tuple[Path, dict[str, object], tuple[BundleFile, ...], BundleFile]:
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
        receipt = _decode_json(receipt_raw)
        if receipt.get("archive_sha256") != digest:
            raise ValueError("digest")
        header = receiver.TransferHeader(
            int(receipt.get("archive_bytes", 1)),
            digest,
            int(receipt.get("protocol", host_contract.TRANSFER_PROTOCOL_VERSION)),
        )
        receiver._verify_object(object_root, header, manifest, files)
        receiver._validate_catalogue_selection(object_root, manifest, files)
    except (
        KeyError,
        TypeError,
        ValueError,
        receiver.TransferError,
    ) as error:
        raise SpeakerReviewProcessingError("private object rejected") from error
    if (
        manifest.get("purpose") != review_contract.REVIEW_PURPOSE
        or manifest.get("season_number") != review_contract.REVIEW_SEASON_NUMBER
    ):
        raise SpeakerReviewProcessingError("private object rejected")
    install_receipt = BundleFile(
        host_contract.INSTALL_RECEIPT_FILENAME,
        len(receipt_raw),
        hashlib.sha256(receipt_raw).hexdigest(),
    )
    return object_root, manifest, files, install_receipt


def _decode_json(raw: bytes) -> dict[str, object]:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=reject_duplicates,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")),
    )
    if not isinstance(value, dict):
        raise ValueError("object required")
    return value


def _source_files(
    files: tuple[BundleFile, ...],
    install_receipt: BundleFile,
) -> dict[str, BundleFile | None]:
    policy = DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION
    return {
        policy.manifest_filename: None,
        host_contract.INSTALL_RECEIPT_FILENAME: install_receipt,
        **{item.path: item for item in files},
    }


def _expected_directories(names: set[str]) -> set[str]:
    directories = {".", _RUN_DIRECTORY_NAME}
    for name in names:
        current = PurePosixPath(name).parent
        while current != PurePosixPath("."):
            directories.add(current.as_posix())
            current = current.parent
    return directories


def _copy_source_file(
    source: Path,
    destination: Path,
    *,
    expected: BundleFile | None,
) -> None:
    input_descriptor = output_descriptor = -1
    copied = 0
    digest = hashlib.sha256()
    try:
        before = source.lstat()
        if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or before.st_nlink != 1:
            raise SpeakerReviewProcessingError("private object changed")
        input_descriptor = os.open(
            source,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(input_descriptor)
        output_descriptor = os.open(
            destination,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            _SOURCE_STAGING_FILE_MODE,
        )
        with (
            os.fdopen(input_descriptor, "rb", closefd=True) as input_stream,
            os.fdopen(output_descriptor, "wb", closefd=True) as output_stream,
        ):
            input_descriptor = output_descriptor = -1
            while chunk := input_stream.read(_READ_CHUNK_BYTES):
                copied += len(chunk)
                digest.update(chunk)
                output_stream.write(chunk)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        after = source.lstat()
        target = destination.lstat()
    except SpeakerReviewProcessingError:
        raise
    except OSError as error:
        raise SpeakerReviewProcessingError("workspace unavailable") from error
    finally:
        if input_descriptor >= 0:
            os.close(input_descriptor)
        if output_descriptor >= 0:
            os.close(output_descriptor)
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_nlink,
    )
    opened_identity = (
        opened.st_dev,
        opened.st_ino,
        opened.st_size,
        opened.st_mtime_ns,
        opened.st_nlink,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_nlink,
    )
    if (
        before_identity != opened_identity
        or opened_identity != after_identity
        or copied != opened.st_size
        or target.st_nlink != 1
        or (
            expected is not None
            and (copied != expected.size or digest.hexdigest() != expected.sha256)
        )
    ):
        raise SpeakerReviewProcessingError("private object changed")
    destination.chmod(_SOURCE_FILE_MODE)
    _set_owner(destination, 0, host_contract.SPEAKER_REVIEW_GID)


def _make_source_directory(path: Path, *, mode: int = _SOURCE_DIRECTORY_MODE) -> None:
    try:
        path.mkdir(mode=mode)
        path.chmod(mode)
        _set_owner(path, 0, host_contract.SPEAKER_REVIEW_GID)
    except OSError as error:
        raise SpeakerReviewProcessingError("workspace unavailable") from error


def _verify_source_workspace(
    workspace: Path,
    manifest: dict[str, object],
    files: tuple[BundleFile, ...],
    install_receipt: BundleFile,
) -> None:
    expected_files = _source_files(files, install_receipt)
    expected_directories = _expected_directories(set(expected_files))
    try:
        root_metadata = workspace.lstat()
    except OSError as error:
        raise SpeakerReviewProcessingError("workspace unavailable") from error
    if (
        not stat.S_ISDIR(root_metadata.st_mode)
        or stat.S_ISLNK(root_metadata.st_mode)
        or stat.S_IMODE(root_metadata.st_mode) != _SOURCE_DIRECTORY_MODE
        or not _expected_owner(root_metadata, 0, host_contract.SPEAKER_REVIEW_GID)
    ):
        raise SpeakerReviewProcessingError("workspace unavailable")
    observed_files: set[str] = set()
    observed_directories: set[str] = {"."}
    for directory, names, filenames in os.walk(workspace, followlinks=False):
        directory_path = Path(directory)
        relative = directory_path.relative_to(workspace).as_posix() or "."
        metadata = directory_path.lstat()
        expected_mode = 0o550 if relative == _RUN_DIRECTORY_NAME else _SOURCE_DIRECTORY_MODE
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != expected_mode
            or not _expected_owner(metadata, 0, host_contract.SPEAKER_REVIEW_GID)
        ):
            raise SpeakerReviewProcessingError("workspace unavailable")
        observed_directories.add(relative)
        for name in names:
            candidate = directory_path / name
            child = candidate.lstat()
            if not stat.S_ISDIR(child.st_mode) or stat.S_ISLNK(child.st_mode):
                raise SpeakerReviewProcessingError("workspace unavailable")
            observed_directories.add(candidate.relative_to(workspace).as_posix())
        for name in filenames:
            candidate = directory_path / name
            child = candidate.lstat()
            if (
                not stat.S_ISREG(child.st_mode)
                or stat.S_ISLNK(child.st_mode)
                or child.st_nlink != 1
                or stat.S_IMODE(child.st_mode) != _SOURCE_FILE_MODE
                or not _expected_owner(child, 0, host_contract.SPEAKER_REVIEW_GID)
            ):
                raise SpeakerReviewProcessingError("workspace unavailable")
            observed_files.add(candidate.relative_to(workspace).as_posix())
    if observed_files != set(expected_files) or observed_directories != expected_directories:
        raise SpeakerReviewProcessingError("workspace unavailable")
    for name, descriptor in expected_files.items():
        content = workspace.joinpath(*PurePosixPath(name).parts).read_bytes()
        if descriptor is not None and (
            len(content) != descriptor.size
            or hashlib.sha256(content).hexdigest() != descriptor.sha256
        ):
            raise SpeakerReviewProcessingError("workspace changed")
        if name == DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION.manifest_filename:
            try:
                if _decode_manifest(content) != manifest:
                    raise SpeakerReviewProcessingError("workspace changed")
            except ValueError as error:
                raise SpeakerReviewProcessingError("workspace changed") from error


def _materialize_source(
    object_root: Path,
    manifest: dict[str, object],
    files: tuple[BundleFile, ...],
    digest: str,
    install_receipt: BundleFile,
) -> Path:
    root = host_contract.SPEAKER_REVIEW_SOURCE_ROOT
    _require_root_private_directory(root)
    final = root / f"{_OBJECT_DIRECTORY_PREFIX}{digest}"
    if os.path.lexists(final):
        _verify_source_workspace(final, manifest, files, install_receipt)
        return final
    stage: Path | None = None
    try:
        stage = Path(tempfile.mkdtemp(prefix=_STAGING_PREFIX, dir=root))
        stage.chmod(_SOURCE_DIRECTORY_MODE)
        _set_owner(stage, 0, host_contract.SPEAKER_REVIEW_GID)
        sources = _source_files(files, install_receipt)
        for name in sorted(sources):
            destination = stage.joinpath(*PurePosixPath(name).parts)
            current = stage
            for part in PurePosixPath(name).parts[:-1]:
                current /= part
                if not current.exists():
                    _make_source_directory(current)
            _copy_source_file(
                object_root.joinpath(*PurePosixPath(name).parts),
                destination,
                expected=sources[name],
            )
        placeholder = stage / _RUN_DIRECTORY_NAME
        _make_source_directory(placeholder, mode=0o550)
        _verify_source_workspace(stage, manifest, files, install_receipt)
        try:
            _rename_no_replace(stage, final)
            stage = None
        except OSError as error:
            if error.errno != errno.EEXIST:
                raise SpeakerReviewProcessingError("workspace unavailable") from error
            _verify_source_workspace(final, manifest, files, install_receipt)
        return final
    except SpeakerReviewProcessingError:
        raise
    except (OSError, StopIteration) as error:
        raise SpeakerReviewProcessingError("workspace unavailable") from error
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)


def _run_mount(digest: str) -> Path:
    root = host_contract.SPEAKER_REVIEW_RUNS_ROOT
    _require_root_private_directory(root)
    container_root = root / f"{_OBJECT_DIRECTORY_PREFIX}{digest}"
    review_runs = container_root / _RUN_DIRECTORY_NAME
    try:
        if not os.path.lexists(container_root):
            container_root.mkdir(mode=0o700)
            container_root.chmod(0o700)
            _set_owner(container_root, 0, 0)
        else:
            _require_root_private_directory(container_root)
        if not os.path.lexists(review_runs):
            review_runs.mkdir(mode=_RUN_PARENT_MODE)
            review_runs.chmod(_RUN_PARENT_MODE)
            _set_owner(
                review_runs,
                host_contract.SPEAKER_REVIEW_UID,
                host_contract.SPEAKER_REVIEW_GID,
            )
        metadata = review_runs.lstat()
    except SpeakerReviewProcessingError:
        raise
    except OSError as error:
        raise SpeakerReviewProcessingError("workspace unavailable") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != _RUN_PARENT_MODE
        or not _expected_owner(
            metadata,
            host_contract.SPEAKER_REVIEW_UID,
            host_contract.SPEAKER_REVIEW_GID,
        )
    ):
        raise SpeakerReviewProcessingError("workspace unavailable")
    return review_runs


def _read_bounded(stream: IO[bytes]) -> bytes:
    try:
        return stream.read(review_contract.REVIEW_OUTPUT_MAX_BYTES + 1)
    finally:
        stream.close()


def _terminate_worker(process: subprocess.Popen[bytes]) -> None:
    """Terminate Compose and every descendant in its dedicated process group."""

    kill_process_group = getattr(os, "killpg", None)
    kill_signal = getattr(signal, "SIGKILL", None)
    if (
        os.name != "posix"
        or not callable(kill_process_group)
        or kill_signal is None
        or not isinstance(getattr(process, "pid", None), int)
    ):
        process.kill()
        process.wait()
        return

    process_group_id = process.pid
    try:
        kill_process_group(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        process.terminate()
    try:
        process.wait(timeout=_WORKER_TERMINATION_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        kill_process_group(process_group_id, kill_signal)
    except ProcessLookupError:
        return
    except OSError:
        process.kill()
    process.wait()


def _cleanup_compose_worker(arguments: list[str], release: Path) -> None:
    """Remove any one-off Compose container left after an interrupted client."""

    if os.name != "posix":
        return
    try:
        run_index = arguments.index("run")
        service = arguments[-1]
        cleanup_arguments = [
            *arguments[:run_index],
            "rm",
            "--force",
            "--stop",
            service,
        ]
    except ValueError:
        return
    cleanup_commands = (
        cleanup_arguments,
        ["docker", "rm", "--force", host_contract.SPEAKER_REVIEW_CONTAINER_NAME],
    )
    for cleanup_command in cleanup_commands:
        try:
            subprocess.run(
                cleanup_command,
                cwd=release,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                check=False,
                timeout=_WORKER_TERMINATION_GRACE_SECONDS,
            )
        except (OSError, subprocess.SubprocessError):
            # Cleanup is best effort; always attempt the exact-name removal too.
            continue


def _run_worker(release: Path, source: Path, review_runs: Path) -> dict[str, object]:
    compose = release / "deploy/compose.yaml"
    arguments = [
        "docker",
        "compose",
        "--progress",
        "quiet",
        "--env-file",
        os.fspath(host_contract.DEV_ENV_FILE),
        "--profile",
        "corpus-speaker-review",
        "-f",
        os.fspath(compose),
        "run",
        "--name",
        host_contract.SPEAKER_REVIEW_CONTAINER_NAME,
        "--rm",
        "--no-TTY",
        "--no-deps",
        "--pull",
        "never",
        "--user",
        f"{host_contract.SPEAKER_REVIEW_UID}:{host_contract.SPEAKER_REVIEW_GID}",
        "--volume",
        f"{source.as_posix()}:/private-corpus:ro",
        "--volume",
        f"{review_runs.as_posix()}:/private-corpus/review-runs:rw",
        "corpus-speaker-review-prepare",
    ]
    process: subprocess.Popen[bytes] | None = None
    process_terminated = False
    _cleanup_compose_worker(arguments, release)
    try:
        if os.name == "posix":
            process = subprocess.Popen(
                arguments,
                cwd=release,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=True,
            )
        else:
            process = subprocess.Popen(
                arguments,
                cwd=release,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
        if process.stdout is None or process.stderr is None:
            raise SpeakerReviewProcessingError("worker failed")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            stdout_future = executor.submit(_read_bounded, process.stdout)
            stderr_future = executor.submit(_read_bounded, process.stderr)
            try:
                returncode = process.wait(timeout=_WORKER_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as error:
                _terminate_worker(process)
                process_terminated = True
                stdout_future.result(timeout=5)
                stderr_future.result(timeout=5)
                raise SpeakerReviewProcessingError("worker failed") from error
            stdout = stdout_future.result(timeout=5)
            stderr = stderr_future.result(timeout=5)
        if (
            returncode != 0
            or stderr
            or len(stdout) > review_contract.REVIEW_OUTPUT_MAX_BYTES
            or len(stderr) > review_contract.REVIEW_OUTPUT_MAX_BYTES
        ):
            raise SpeakerReviewProcessingError("worker failed")
        decoded = _decode_json(stdout)
        if review_contract.canonical_json(decoded) != stdout:
            raise ValueError("noncanonical")
        return review_contract.validate_aggregate(
            decoded,
            operation="prepare",
            status="prepared",
        )
    except SpeakerReviewProcessingError:
        raise
    except (
        OSError,
        subprocess.SubprocessError,
        TimeoutError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
    ) as error:
        raise SpeakerReviewProcessingError("worker failed") from error
    finally:
        if process is not None and not process_terminated and process.poll() is None:
            try:
                _terminate_worker(process)
            except (OSError, subprocess.SubprocessError):
                pass
        _cleanup_compose_worker(arguments, release)


def _configuration_sha256(release: Path = _RELEASE_ROOT) -> str:
    """Bind receipts to exact tracked model, prompt, cost, and filesystem policy."""

    digest = hashlib.sha256()
    try:
        for locator in _CONFIGURATION_BINDING_FILES:
            path = release.joinpath(*PurePosixPath(locator).parts)
            metadata = path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_size > _RUN_STATE_MAX_BYTES
            ):
                raise SpeakerReviewProcessingError("configuration unavailable")
            content = path.read_bytes()
            if len(content) != metadata.st_size:
                raise SpeakerReviewProcessingError("configuration unavailable")
            encoded_locator = locator.encode("ascii")
            digest.update(len(encoded_locator).to_bytes(4, "big"))
            digest.update(encoded_locator)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
    except SpeakerReviewProcessingError:
        raise
    except (OSError, UnicodeError, ValueError) as error:
        raise SpeakerReviewProcessingError("configuration unavailable") from error
    return digest.hexdigest()


def _receipt_path(digest: str) -> Path:
    return host_contract.SPEAKER_REVIEW_RECEIPTS_ROOT / (
        f"{_RECEIPT_PREFIX}{digest}{_RECEIPT_SUFFIX}"
    )


def _write_receipt(path: Path, payload: dict[str, object]) -> None:
    _require_root_private_directory(path.parent)
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
        temporary = Path(name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(review_contract.canonical_json(payload))
            output.flush()
            os.fsync(output.fileno())
        _set_owner(temporary, 0, 0)
        _rename_no_replace(temporary, path)
        temporary = None
    except OSError as error:
        if error.errno == errno.EEXIST:
            raise SpeakerReviewProcessingError("receipt conflict") from error
        raise SpeakerReviewProcessingError("receipt unavailable") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _load_receipt(path: Path) -> dict[str, object]:
    try:
        raw = receiver._regular_root_file(
            path,
            mode=0o600,
            max_bytes=review_contract.REVIEW_OUTPUT_MAX_BYTES,
        )
        receipt = _decode_json(raw)
        if set(receipt) != _RECEIPT_KEYS or review_contract.canonical_json(receipt) != raw:
            raise ValueError("invalid receipt")
        return receipt
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
        receiver.TransferError,
    ) as error:
        raise SpeakerReviewProcessingError("receipt invalid") from error


def _expected_receipt(
    *,
    digest: str,
    manifest: dict[str, object],
    release: Path,
    image_reference: str,
    result: object,
    artifact_set_sha256: str,
    artifact_file_count: int,
) -> dict[str, object]:
    return {
        "archive_sha256": digest,
        "artifact_file_count": artifact_file_count,
        "artifact_set_sha256": artifact_set_sha256,
        "catalogue_sha256": manifest["source_catalogue_sha256"],
        "configuration_sha256": _configuration_sha256(release),
        "image_reference": image_reference,
        "release_sha": release.name,
        "result": result,
        "schema_version": _RECEIPT_SCHEMA_VERSION,
    }


def _verify_receipt_binding(
    receipt: dict[str, object],
    *,
    digest: str,
    manifest: dict[str, object],
    release: Path,
    image_reference: str,
) -> dict[str, object]:
    receipt_artifact_count = receipt.get("artifact_file_count")
    expected = _expected_receipt(
        digest=digest,
        manifest=manifest,
        release=release,
        image_reference=image_reference,
        result=receipt.get("result"),
        artifact_set_sha256=str(receipt.get("artifact_set_sha256", "")),
        artifact_file_count=(receipt_artifact_count if type(receipt_artifact_count) is int else -1),
    )
    artifact_digest = receipt.get("artifact_set_sha256")
    artifact_count = receipt.get("artifact_file_count")
    if (
        receipt != expected
        or not isinstance(artifact_digest, str)
        or len(artifact_digest) != 64
        or any(character not in "0123456789abcdef" for character in artifact_digest)
        or type(artifact_count) is not int
        or artifact_count <= 0
    ):
        raise SpeakerReviewProcessingError("receipt invalid")
    result = receipt["result"]
    try:
        validated = review_contract.validate_aggregate(result, operation="prepare")
    except ValueError as error:
        raise SpeakerReviewProcessingError("receipt invalid") from error
    return validated


def _validate_persisted_run(
    review_runs: Path,
    aggregate: dict[str, object],
) -> tuple[str, int]:
    run_id = str(aggregate["run_id"])
    part_count = aggregate["primary_part_count"]
    if type(part_count) is not int or part_count <= 0:
        raise SpeakerReviewProcessingError("prepared run invalid")
    expected_files = {
        _CANDIDATES_FILENAME,
        _SOURCE_MANIFEST_FILENAME,
        _RUN_STATE_FILENAME,
        *(
            _PRIMARY_REQUEST_FILENAME_TEMPLATE.format(part_number=part_number)
            for part_number in range(1, part_count + 1)
        ),
    }
    try:
        run_directory = review_runs / run_id
        metadata = run_directory.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or not _expected_owner(
                metadata,
                host_contract.SPEAKER_REVIEW_UID,
                host_contract.SPEAKER_REVIEW_GID,
            )
            or run_directory.resolve(strict=True).parent != review_runs.resolve(strict=True)
        ):
            raise ValueError("run directory")
        entries = tuple(run_directory.iterdir())
        if {entry.name for entry in entries} != expected_files:
            raise ValueError("artifact inventory")
        contents = {
            entry.name: _stable_worker_file(
                entry,
                maximum=(
                    _RUN_STATE_MAX_BYTES
                    if entry.name == _RUN_STATE_FILENAME
                    else _RUN_ARTIFACT_MAX_BYTES
                ),
            )
            for entry in entries
        }
        if sum(len(content) for content in contents.values()) > _RUN_ARTIFACT_TOTAL_MAX_BYTES:
            raise ValueError("artifact size")
        state = _decode_json(contents[_RUN_STATE_FILENAME])
    except Exception as error:
        raise SpeakerReviewProcessingError("prepared run invalid") from error
    if (
        state.get("status") != "prepared"
        or state.get("run_id") != run_id
        or type(state.get("candidate_count")) is not int
        or state.get("candidate_count") != aggregate["candidate_count"]
        or type(state.get("primary_part_count")) is not int
        or state.get("primary_part_count") != aggregate["primary_part_count"]
        or type(state.get("estimated_primary_cost_usd")) not in (int, float)
        or round(float(state["estimated_primary_cost_usd"]), 6)
        != aggregate["estimated_primary_cost_usd"]
    ):
        raise SpeakerReviewProcessingError("prepared run invalid")
    artifact_digest = hashlib.sha256()
    for name in sorted(contents):
        encoded_name = name.encode("ascii")
        content = contents[name]
        artifact_digest.update(len(encoded_name).to_bytes(4, "big"))
        artifact_digest.update(encoded_name)
        artifact_digest.update(len(content).to_bytes(8, "big"))
        artifact_digest.update(content)
    return artifact_digest.hexdigest(), len(contents)


def _stable_worker_file(path: Path, *, maximum: int) -> bytes:
    descriptor = -1
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > maximum
            or stat.S_IMODE(before.st_mode) != 0o600
            or not _expected_owner(
                before,
                host_contract.SPEAKER_REVIEW_UID,
                host_contract.SPEAKER_REVIEW_GID,
            )
        ):
            raise SpeakerReviewProcessingError("prepared run invalid")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        content = os.read(descriptor, maximum + 1)
        after = path.lstat()
    except SpeakerReviewProcessingError:
        raise
    except OSError as error:
        raise SpeakerReviewProcessingError("prepared run invalid") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_nlink,
    )
    opened_identity = (
        opened.st_dev,
        opened.st_ino,
        opened.st_size,
        opened.st_mtime_ns,
        opened.st_nlink,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_nlink,
    )
    if (
        before_identity != opened_identity
        or opened_identity != after_identity
        or len(content) != opened.st_size
    ):
        raise SpeakerReviewProcessingError("prepared run invalid")
    return content


def _validate_aggregate_for_manifest(
    aggregate: dict[str, object],
    manifest: dict[str, object],
) -> None:
    if (
        aggregate["file_count"] != manifest["file_count"]
        or aggregate["total_bytes"] != manifest["total_bytes"]
    ):
        raise SpeakerReviewProcessingError("worker result invalid")


def _validation_aggregate(manifest: dict[str, object]) -> dict[str, object]:
    return {
        "file_count": manifest["file_count"],
        "operation": "validate",
        "purpose": review_contract.REVIEW_PURPOSE,
        "season_number": review_contract.REVIEW_SEASON_NUMBER,
        "status": "validated",
        "total_bytes": manifest["total_bytes"],
    }


def process_request(request: dict[str, object]) -> dict[str, object]:
    try:
        request = review_contract.validate_request(request)
    except (TypeError, ValueError) as error:
        raise SpeakerReviewProcessingError("invalid request") from error
    digest = str(request["archive_sha256"])
    operation = str(request["operation"])
    release, _catalogue = host_runtime._active_release()
    host_runtime._verify_release_image(release)
    image_reference = host_runtime._release_image_reference(release)
    object_root, manifest, files, install_receipt = _verified_object(digest)
    if operation == "validate":
        result = _validation_aggregate(manifest)
        return review_contract.validate_aggregate(
            result,
            operation=operation,
            status="validated",
        )

    receipt_path = _receipt_path(digest)
    if not os.path.lexists(receipt_path) and operation == "status":
        raise SpeakerReviewProcessingError("receipt unavailable")
    if os.path.lexists(receipt_path):
        receipt = _load_receipt(receipt_path)
        result = _verify_receipt_binding(
            receipt,
            digest=digest,
            manifest=manifest,
            release=release,
            image_reference=image_reference,
        )
        requested_run_id = request.get("run_id")
        if operation == "status" and requested_run_id != result["run_id"]:
            raise SpeakerReviewProcessingError("receipt invalid")
        # Replay/status must revalidate the immutable source mount as well as
        # the receipt.  A receipt binds the source manifest digest, but does
        # not prove that a previously materialized mount was not tampered with
        # after the original preparation.
        source = _materialize_source(object_root, manifest, files, digest, install_receipt)
        _verify_source_workspace(source, manifest, files, install_receipt)
        review_runs = _run_mount(digest)
        artifact_digest, artifact_count = _validate_persisted_run(review_runs, result)
        if (
            receipt["artifact_set_sha256"] != artifact_digest
            or receipt["artifact_file_count"] != artifact_count
        ):
            raise SpeakerReviewProcessingError("prepared run invalid")
        return {
            **result,
            "operation": operation,
            "status": "prepared" if operation == "status" else "already_prepared",
        }

    source = _materialize_source(object_root, manifest, files, digest, install_receipt)
    review_runs = _run_mount(digest)
    # Revalidate the installed object and copied source immediately before execution.
    _verified_object(digest)
    _verify_source_workspace(source, manifest, files, install_receipt)
    result = _run_worker(release, source, review_runs)
    _validate_aggregate_for_manifest(result, manifest)
    _verified_object(digest)
    _verify_source_workspace(source, manifest, files, install_receipt)
    artifact_digest, artifact_count = _validate_persisted_run(review_runs, result)
    receipt = _expected_receipt(
        digest=digest,
        manifest=manifest,
        release=release,
        image_reference=image_reference,
        result=result,
        artifact_set_sha256=artifact_digest,
        artifact_file_count=artifact_count,
    )
    _write_receipt(receipt_path, receipt)
    return result


def _error_payload() -> bytes:
    return review_contract.canonical_json({"error": "speaker_review_rejected", "status": "error"})


def main() -> int:
    try:
        if os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0:
            raise SpeakerReviewProcessingError("not root")
        if os.environ.get("SUDO_USER") != host_contract.CORPUS_USER:
            raise SpeakerReviewProcessingError("invalid caller")
        request = _read_request(sys.stdin.buffer)
        result = process_request(request)
        review_contract.validate_aggregate(
            result,
            operation=str(request["operation"]),
            status=str(result["status"]),
        )
        payload = review_contract.canonical_json(result)
        if len(payload) > review_contract.REVIEW_OUTPUT_MAX_BYTES:
            raise SpeakerReviewProcessingError("result too large")
        sys.stdout.buffer.write(payload)
        return 0
    except Exception:
        sys.stderr.buffer.write(_error_payload())
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
