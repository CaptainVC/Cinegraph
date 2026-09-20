"""Root coordinator for the provider-free adjudication-result checkpoint."""

from __future__ import annotations

import concurrent.futures
import hashlib
import importlib
import json
import os
import platform
import re
import signal
import stat
import subprocess
import sys
import types
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal
from enum import Enum
from pathlib import Path
from typing import BinaryIO, Final, Mapping

_ROOT = Path(__file__).resolve().parents[1]
if os.fspath(_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(_ROOT))

from scripts import (  # noqa: E402
    private_speaker_review_adjudication_result_processing_contract as contract,
)
from scripts import (  # noqa: E402
    private_speaker_review_adjudication_result_processing_host_contract as host,
)
from scripts import (  # noqa: E402
    run_private_speaker_review_fourth_adjudication_observation as predecessor,
)
from scripts import run_private_speaker_review_next_primary as runtime  # noqa: E402
from scripts import (  # noqa: E402
    run_private_speaker_review_primary_result_processing as primary_processing,
)

worker: types.ModuleType | types.SimpleNamespace | None
if sys.flags.no_site:
    # The privileged host process is deliberately stdlib-only.  The actual
    # application workflow, LangGraph, and every optional provider dependency
    # remain inside the unprivileged Compose worker.
    worker = None
else:
    worker = importlib.import_module(
        "scripts.process_private_speaker_review_adjudication_results_workspace"
    )


class AdjudicationResultProcessingError(RuntimeError):
    """Generic rejection which never exposes private evidence."""


ROOT_UID: Final = 0
ROOT_GID: Final = 0
WORKER_OWNER: Final = (host.UID_IN_CONTAINER, host.GID_IN_CONTAINER)
RUNS_ROOT: Final = host.SPEAKER_REVIEW_RUNS_ROOT
SOURCE_ROOT: Final = host.SPEAKER_REVIEW_SOURCE_ROOT
AUTHORIZATION_ROOT: Final = host.REVIEW_AUTHORIZATION_ROOT
RECEIPTS_ROOT: Final = host.REVIEW_ADJUDICATION_RESULT_PROCESSING_RECEIPTS_ROOT
COMPOSE_PATH: Final = _ROOT / "deploy/compose.yaml"
ENV_FILE: Final = host.ENV_FILE
MAX_RECORD_BYTES: Final = 128 * 1024
MAX_TOTAL_BYTES: Final = 256 * 1024 * 1024
WORKER_TIMEOUT_SECONDS: Final = host.PROCESSING_TIMEOUT_SECONDS - 60
WORKER_KILL_AFTER_SECONDS: Final = host.PROCESSING_KILL_AFTER_SECONDS
STATE_FILENAME: Final = "run-state.json"
_STATE_MUTABLE_FIELDS: Final = frozenset(
    {
        "accepted_by_adjudication",
        "actual_total_cost_usd",
        "final_review_part_count",
        "needs_human",
        "status",
        "updated_at",
    }
)
_INTENT_KEYS: Final = frozenset(
    {
        "archive_sha256",
        "authorization_id",
        "authorization_sha256",
        "configuration_sha256",
        "fourth_observation_intent_sha256",
        "fourth_observation_receipt_sha256",
        "image_reference",
        "maximum_authorized_cost_microusd",
        "operation",
        "pre_digests",
        "pre_directory_identities",
        "pre_hashes",
        "pre_state_binding_sha256",
        "pre_state_sha256",
        "preparation_receipt_sha256",
        "purpose",
        "release_sha",
        "request_sha256",
        "run_id",
        "schema_version",
        "season_number",
        "source_manifest_sha256",
        "status",
    }
)


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    """Six disjoint evidence classes plus directory identities."""

    state: bytes
    artifacts: dict[str, bytes]
    requests: dict[str, bytes]
    journals: dict[str, bytes]
    outputs: dict[str, bytes]
    derived: dict[str, bytes]
    directories: dict[str, tuple[int, int, int, int, int]] = field(default_factory=dict)
    directory_identity: tuple[int, int, int, int, int] | None = None


def _canonical(value: Mapping[str, object]) -> bytes:
    return contract.canonical_json(value)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _micros(value: object) -> int:
    try:
        decimal = Decimal(str(value)) * Decimal(1_000_000)
        if not decimal.is_finite() or decimal < 0:
            raise ValueError
        return int(decimal.to_integral_value(rounding=ROUND_CEILING))
    except (ArithmeticError, TypeError, ValueError) as error:
        raise AdjudicationResultProcessingError("processing cost invalid") from error


def _identity(value: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_nlink,
        stat.S_IMODE(value.st_mode),
        value.st_uid,
        value.st_gid,
    )


def _directory(path: Path, *, mode: int, owner: tuple[int, int]) -> None:
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or path.resolve(strict=True) != path
            or (os.name == "posix" and stat.S_IMODE(metadata.st_mode) != mode)
            or (os.name == "posix" and (metadata.st_uid, metadata.st_gid) != owner)
        ):
            raise OSError
    except OSError as error:
        raise AdjudicationResultProcessingError("processing evidence unavailable") from error


def _stable(path: Path, maximum: int, *, mode: int, owner: tuple[int, int]) -> bytes:
    descriptor = -1
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > maximum
            or (os.name == "posix" and stat.S_IMODE(before.st_mode) != mode)
            or (os.name == "posix" and (before.st_uid, before.st_gid) != owner)
        ):
            raise OSError
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = path.lstat()
        if _identity(before) != _identity(opened) or _identity(opened) != _identity(after):
            raise OSError
        if len(raw) != before.st_size:
            raise OSError
        return raw
    except OSError as error:
        raise AdjudicationResultProcessingError("processing evidence unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _decode(raw: bytes) -> dict[str, object]:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise AdjudicationResultProcessingError("processing receipt invalid") from error
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise AdjudicationResultProcessingError("processing receipt invalid")
    return value


def _decode_document(raw: bytes) -> dict[str, object]:
    """Decode a persisted JSON document without imposing wire formatting."""

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise AdjudicationResultProcessingError("processing document invalid") from error
    if not isinstance(value, dict):
        raise AdjudicationResultProcessingError("processing document invalid")
    return value


def _read_request(stream: BinaryIO) -> dict[str, object]:
    raw = stream.readline(contract.REQUEST_MAX_BYTES + 1)
    if stream.read(1):
        raise AdjudicationResultProcessingError("invalid processing request")
    try:
        return contract.parse_request(raw)
    except (TypeError, ValueError) as error:
        raise AdjudicationResultProcessingError("invalid processing request") from error


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _repair_linked_publication(path: Path) -> None:
    """Repair only a final/pending pair linked to the same safe inode."""

    pending = path.with_name(f".{path.name}.pending")
    if not os.path.lexists(pending):
        return
    try:
        published = path.lstat()
        staged = pending.lstat()
        if (
            not stat.S_ISREG(published.st_mode)
            or not stat.S_ISREG(staged.st_mode)
            or stat.S_ISLNK(published.st_mode)
            or stat.S_ISLNK(staged.st_mode)
            or (published.st_dev, published.st_ino) != (staged.st_dev, staged.st_ino)
            or published.st_nlink != 2
            or staged.st_nlink != 2
            or (os.name == "posix" and stat.S_IMODE(published.st_mode) != 0o600)
            or (os.name == "posix" and (published.st_uid, published.st_gid) != (ROOT_UID, ROOT_GID))
        ):
            raise OSError
        pending.unlink()
        _fsync_directory(path.parent)
    except OSError as error:
        raise AdjudicationResultProcessingError("processing receipt conflict") from error


def _read_root_record(path: Path) -> tuple[dict[str, object], str]:
    _repair_linked_publication(path)
    raw = _stable(path, MAX_RECORD_BYTES, mode=0o600, owner=(ROOT_UID, ROOT_GID))
    return _decode(raw), _sha(raw)


def _write_once(path: Path, value: Mapping[str, object]) -> str:
    encoded = _canonical(value)
    if len(encoded) > MAX_RECORD_BYTES:
        raise AdjudicationResultProcessingError("processing receipt unavailable")
    _directory(path.parent, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    pending = path.with_name(f".{path.name}.pending")
    if os.path.lexists(path):
        _repair_linked_publication(path)
        existing, existing_sha = _read_root_record(path)
        if _canonical(existing) != encoded:
            raise AdjudicationResultProcessingError("processing receipt conflict")
        return existing_sha
    if os.path.lexists(pending):
        raise AdjudicationResultProcessingError("processing receipt conflict")
    descriptor = -1
    try:
        descriptor = os.open(
            pending,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_directory(path.parent)
        os.link(pending, path, follow_symlinks=False)
        _fsync_directory(path.parent)
        pending.unlink()
        _fsync_directory(path.parent)
        return _sha(encoded)
    except FileExistsError as error:
        raise AdjudicationResultProcessingError("processing receipt conflict") from error
    except OSError as error:
        raise AdjudicationResultProcessingError("processing receipt unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _authorization(request: Mapping[str, object]) -> str:
    _directory(AUTHORIZATION_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    raw = _stable(
        AUTHORIZATION_ROOT / f"{request['authorization_id']}.json",
        contract.REQUEST_MAX_BYTES,
        mode=0o600,
        owner=(ROOT_UID, ROOT_GID),
    )
    try:
        if contract.parse_request(raw) != dict(request):
            raise ValueError
    except (TypeError, ValueError) as error:
        raise AdjudicationResultProcessingError("processing authorization invalid") from error
    return _sha(raw)


def _run_paths(
    request: Mapping[str, object],
) -> tuple[Path, Path, Path, dict[str, object], str]:
    _directory(RUNS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    object_root = RUNS_ROOT / f"sha256-{request['archive_sha256']}"
    runs = object_root / "review-runs"
    run = runs / str(request["run_id"])
    _directory(object_root, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    _directory(runs, mode=0o700, owner=WORKER_OWNER)
    _directory(run, mode=0o700, owner=WORKER_OWNER)
    if run.resolve(strict=True).parent != runs.resolve(strict=True):
        raise AdjudicationResultProcessingError("processing run invalid")
    source = SOURCE_ROOT / f"sha256-{request['archive_sha256']}"
    _directory(SOURCE_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    _directory(source, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    try:
        preparation, preparation_sha = runtime._validate_preparation(request)
        verified_source = primary_processing._source_workspace(request)
        if verified_source != source:
            raise ValueError
    except Exception as error:
        raise AdjudicationResultProcessingError("processing source unavailable") from error
    return source, runs, run, preparation, preparation_sha


def _worker_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_nlink,
    )


def _validate_worker_path(metadata: os.stat_result, *, directory: bool) -> None:
    if directory:
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise OSError
        expected_mode = 0o700
    else:
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_nlink != 1
        ):
            raise OSError
        expected_mode = 0o600
    if os.name == "posix" and (
        stat.S_IMODE(metadata.st_mode) != expected_mode
        or (metadata.st_uid, metadata.st_gid) != WORKER_OWNER
    ):
        raise OSError


def _isolated_digest(contents: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(contents):
        encoded = name.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(contents[name]).to_bytes(8, "big"))
        digest.update(contents[name])
    return digest.hexdigest()


def _isolated_expected_directory_link_count(
    identity: tuple[int, ...], directory: str, created_directories: set[str]
) -> int:
    if os.name != "posix":
        return identity[4]
    return identity[4] + sum(
        1 for created in created_directories if (created.rpartition("/")[0] or ".") == directory
    )


def _isolated_inventory(run_directory: Path) -> dict[str, object]:
    try:
        root_before = run_directory.lstat()
        _validate_worker_path(root_before, directory=True)
        groups: dict[str, object] = {
            "artifacts": {},
            "requests": {},
            "journals": {},
            "outputs": {},
            "derived": {},
            "directories": {},
        }
        directory_identities: dict[str, tuple[int, int, int, int, int]] = {
            ".": _worker_identity(root_before)
        }
        total = 0
        for current, directories, filenames in os.walk(run_directory, followlinks=False):
            current_path = Path(current)
            current_relative = current_path.relative_to(run_directory).as_posix()
            if current_relative != ".":
                current_metadata = current_path.lstat()
                _validate_worker_path(current_metadata, directory=True)
                directory_identities[current_relative] = _worker_identity(current_metadata)
            for directory in directories:
                child = current_path / directory
                child_metadata = child.lstat()
                _validate_worker_path(child_metadata, directory=True)
                directory_identities[child.relative_to(run_directory).as_posix()] = (
                    _worker_identity(child_metadata)
                )
            for name in filenames:
                path = current_path / name
                relative = path.relative_to(run_directory).as_posix()
                raw = _stable(path, 64 * 1024 * 1024, mode=0o600, owner=WORKER_OWNER)
                total += len(raw)
                if total > MAX_TOTAL_BYTES:
                    raise OSError
                if relative == STATE_FILENAME:
                    groups["state"] = raw
                elif name.startswith(".") and "-submission-" in name:
                    groups["journals"][relative] = raw  # type: ignore[index]
                elif name.endswith("-requests.jsonl"):
                    groups["requests"][relative] = raw  # type: ignore[index]
                elif name.endswith("-output.jsonl") or name.endswith("-api-errors.jsonl"):
                    groups["outputs"][relative] = raw  # type: ignore[index]
                elif name in {"candidates.jsonl", "source-manifest.json"}:
                    groups["artifacts"][relative] = raw  # type: ignore[index]
                else:
                    groups["derived"][relative] = raw  # type: ignore[index]
        if "state" not in groups:
            raise OSError
        root_after = run_directory.lstat()
        _validate_worker_path(root_after, directory=True)
        if _worker_identity(root_after) != directory_identities["."]:
            raise OSError
        for name, identity in directory_identities.items():
            path = run_directory if name == "." else run_directory / name
            metadata = path.lstat()
            _validate_worker_path(metadata, directory=True)
            if _worker_identity(metadata) != identity:
                raise OSError
        groups["directory_identity"] = directory_identities.pop(".")
        groups["directories"] = directory_identities
        return groups
    except (OSError, RuntimeError) as error:
        raise AdjudicationResultProcessingError("processing inventory invalid") from error


class _IsolatedStatus(str, Enum):
    PREPARED = "prepared"
    PRIMARY_SUBMITTED = "primary_submitted"
    PRIMARY_PART_COMPLETED = "primary_part_completed"
    ADJUDICATION_PREPARED = "adjudication_prepared"
    ADJUDICATION_SUBMITTED = "adjudication_submitted"
    ADJUDICATION_PART_COMPLETED = "adjudication_part_completed"
    FINAL_REVIEW_PREPARED = "final_review_prepared"
    FINAL_REVIEW_SUBMITTED = "final_review_submitted"
    COMPLETED = "completed"
    NEEDS_HUMAN = "needs_human"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class _IsolatedRunState:
    values: Mapping[str, object]

    def __getattr__(self, name: str) -> object:
        try:
            value = self.values[name]
        except KeyError as error:
            raise AttributeError(name) from error
        if name == "status" and isinstance(value, str):
            return _IsolatedStatus(value)
        if name.endswith("_ids") and isinstance(value, list):
            return tuple(value)
        return value

    def to_dict(self) -> dict[str, object]:
        return dict(self.values)


def _isolated_load_validated_run_state(
    run: Path, _configuration: object
) -> tuple[Path, _IsolatedRunState]:
    del _configuration
    raw = _stable(run / STATE_FILENAME, 64 * 1024, mode=0o600, owner=WORKER_OWNER)
    state = _decode_document(raw)
    expected_keys = predecessor.observation._RUN_STATE_KEYS
    try:
        _IsolatedStatus(str(state["status"]))
        total = (
            float(state["actual_primary_cost_usd"])
            + float(state["actual_adjudication_cost_usd"])
            + float(state["actual_final_review_cost_usd"])
        )
    except (KeyError, TypeError, ValueError) as error:
        raise AdjudicationResultProcessingError("processing run state invalid") from error
    if (
        set(state) != expected_keys
        or state.get("run_id") != run.name
        or state.get("actual_total_cost_usd") != total
    ):
        raise AdjudicationResultProcessingError("processing run state invalid")
    return run, _IsolatedRunState(state)


def _isolated_completed_output_inventory(source_manifest: bytes) -> tuple[set[str], set[str]]:
    try:
        payload = _decode_document(source_manifest)
        sources = payload.get("sources")
        if not isinstance(sources, dict) or not sources:
            raise ValueError
        files: set[str] = set()
        directories = {"reviewed"}
        for raw_name in sources:
            if not isinstance(raw_name, str) or not raw_name.endswith(".script-aligned.srt"):
                raise ValueError
            match = re.search(r"\b(?P<season>\d+)x(?P<episode>\d{2})\b", raw_name)
            if match is None:
                raise ValueError
            directory = f"reviewed/season-{int(match.group('season')):02d}"
            directories.add(directory)
            reviewed_name = raw_name.removesuffix(".script-aligned.srt") + ".automated-reviewed.srt"
            files.add(f"{directory}/{reviewed_name}")
        return files, directories
    except (AttributeError, KeyError, TypeError, UnicodeError, ValueError) as error:
        raise AdjudicationResultProcessingError("processing inventory invalid") from error


def _isolated_validate_inventory_shape(groups: Mapping[str, object], state: object) -> None:
    try:
        state_dict = state.to_dict()  # type: ignore[attr-defined]
        canonical_state = (
            json.dumps(state_dict, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        status = state.status.value  # type: ignore[attr-defined]
        artifacts = groups["artifacts"]
        requests = groups["requests"]
        journals = groups["journals"]
        outputs = groups["outputs"]
        derived = groups["derived"]
        directories = groups["directories"]
        if not all(
            isinstance(value, dict)
            for value in (artifacts, requests, journals, outputs, derived, directories)
        ):
            raise ValueError
        if groups.get("state") != canonical_state or set(artifacts) != {
            "candidates.jsonl",
            "source-manifest.json",
        }:
            raise ValueError
        part_counts = {
            "primary": state.primary_part_count,  # type: ignore[attr-defined]
            "adjudication": state.adjudication_part_count,  # type: ignore[attr-defined]
        }
        if any(type(count) is not int or count <= 0 for count in part_counts.values()):
            raise ValueError
        expected_requests = {
            f"{stage}-part-{index:04d}-requests.jsonl"
            for stage, count in part_counts.items()
            for index in range(1, count + 1)
        }
        expected_journals = {
            f".{stage}-part-{index:04d}-submission-{kind}.json"
            for stage, count in part_counts.items()
            for index in range(1, count + 1)
            for kind in ("intent", "completed")
        }
        if set(journals) != expected_journals:
            raise ValueError
        required_outputs = {
            f"{stage}-part-{index:04d}-output.jsonl"
            for stage, count in part_counts.items()
            for index in range(1, count + 1)
        }
        optional_outputs = {
            f"{stage}-part-{index:04d}-api-errors.jsonl"
            for stage, count in part_counts.items()
            for index in range(1, count + 1)
        }
        if not required_outputs <= set(outputs) <= required_outputs | optional_outputs:
            raise ValueError
        final_count = state.final_review_part_count  # type: ignore[attr-defined]
        if status == "final_review_prepared":
            if type(final_count) is not int or final_count <= 0:
                raise ValueError
        else:
            final_count = 0
        expected_final = {
            f"final-review-part-{index:04d}-requests.jsonl" for index in range(1, final_count + 1)
        }
        if status == "adjudication_part_completed":
            candidate_count = state.candidate_count  # type: ignore[attr-defined]
            if type(candidate_count) is not int or candidate_count <= 0:
                raise ValueError
            allowed_partial_final = {
                f"final-review-part-{index:04d}-requests.jsonl"
                for index in range(1, candidate_count + 1)
            }
            if not expected_requests <= set(requests) <= expected_requests | allowed_partial_final:
                raise ValueError
        else:
            expected_requests.update(expected_final)
            if set(requests) != expected_requests:
                raise ValueError
        required_derived = {
            "primary-verdicts.jsonl",
            "primary-parse-errors.json",
            "primary-decisions.jsonl",
        }
        adjudication_derived = {
            "adjudication-verdicts.jsonl",
            "adjudication-parse-errors.json",
            "final-decisions.jsonl",
        }
        reviewed_files, completed_directories = _isolated_completed_output_inventory(
            artifacts["source-manifest.json"]  # type: ignore[index]
        )
        completion_derived = reviewed_files | {
            "review-ledger.json",
            "calibration-sample.json",
        }
        allowed_derived = required_derived | adjudication_derived
        required_directories: set[str] = set()
        allowed_directories: set[str] = set()
        if status == "completed":
            required_derived |= adjudication_derived | completion_derived
            allowed_derived = set(required_derived)
            required_directories = completed_directories
            allowed_directories = completed_directories
        elif status == "final_review_prepared":
            required_derived |= adjudication_derived
        elif status == "adjudication_part_completed":
            allowed_derived |= completion_derived
            allowed_directories = completed_directories
        else:
            raise ValueError
        if (
            not required_derived <= set(derived) <= allowed_derived
            or not required_directories <= set(directories) <= allowed_directories
        ):
            raise ValueError
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise AdjudicationResultProcessingError("processing inventory invalid") from error


if worker is None:
    worker = types.SimpleNamespace(
        DEFAULT_SPEAKER_REVIEW_CONFIGURATION=object(),
        _digest=_isolated_digest,
        _expected_directory_link_count=_isolated_expected_directory_link_count,
        _inventory=_isolated_inventory,
        _validate_inventory_shape=_isolated_validate_inventory_shape,
        load_validated_run_state=_isolated_load_validated_run_state,
    )


def _inventory(run: Path) -> RunSnapshot:
    try:
        groups = worker._inventory(run)
        state = groups.get("state")
        artifacts = groups.get("artifacts")
        requests = groups.get("requests")
        journals = groups.get("journals")
        outputs = groups.get("outputs")
        derived = groups.get("derived")
        directories = groups.get("directories")
        directory_identity = groups.get("directory_identity")
        if (
            not isinstance(state, bytes)
            or not all(
                isinstance(value, dict)
                for value in (
                    artifacts,
                    requests,
                    journals,
                    outputs,
                    derived,
                    directories,
                )
            )
            or not isinstance(directory_identity, tuple)
            or len(directory_identity) != 5
        ):
            raise ValueError
        snapshot = RunSnapshot(
            state=state,
            artifacts=dict(artifacts),
            requests=dict(requests),
            journals=dict(journals),
            outputs=dict(outputs),
            derived=dict(derived),
            directories=dict(directories),
            directory_identity=directory_identity,
        )
        if (
            len(snapshot.state)
            + sum(
                len(raw)
                for contents in (
                    snapshot.artifacts,
                    snapshot.requests,
                    snapshot.journals,
                    snapshot.outputs,
                    snapshot.derived,
                )
                for raw in contents.values()
            )
            > MAX_TOTAL_BYTES
        ):
            raise ValueError
        return snapshot
    except Exception as error:
        raise AdjudicationResultProcessingError("processing inventory invalid") from error


def _digest(snapshot: RunSnapshot) -> dict[str, str]:
    return {
        "state": _sha(snapshot.state),
        "artifacts": worker._digest(snapshot.artifacts),
        "requests": worker._digest(snapshot.requests),
        "journals": worker._digest(snapshot.journals),
        "outputs": worker._digest(snapshot.outputs),
        "derived": worker._digest(snapshot.derived),
    }


def _hashes(snapshot: RunSnapshot) -> dict[str, dict[str, str]]:
    return {
        name: {path: _sha(raw) for path, raw in sorted(contents.items())}
        for name, contents in (
            ("artifacts", snapshot.artifacts),
            ("requests", snapshot.requests),
            ("journals", snapshot.journals),
            ("outputs", snapshot.outputs),
            ("derived", snapshot.derived),
        )
    }


def _state_model(run: Path, snapshot: RunSnapshot) -> tuple[dict[str, object], object]:
    try:
        canonical, state_model = worker.load_validated_run_state(
            run,
            worker.DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        )
        state = state_model.to_dict()
        canonical_state = (
            json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        if canonical != run or state_model.run_id != run.name or canonical_state != snapshot.state:
            raise ValueError
        worker._validate_inventory_shape(
            {
                "state": snapshot.state,
                "artifacts": snapshot.artifacts,
                "requests": snapshot.requests,
                "journals": snapshot.journals,
                "outputs": snapshot.outputs,
                "derived": snapshot.derived,
                "directories": snapshot.directories,
                "directory_identity": snapshot.directory_identity,
            },
            state_model,
        )
        return state, state_model
    except Exception as error:
        raise AdjudicationResultProcessingError("processing run state invalid") from error


def _validate_predecessors(
    request: Mapping[str, object], run: Path, state_model: object
) -> tuple[str, str]:
    try:
        contents, state, predecessor_state_model = predecessor._inventory(run)
        if predecessor_state_model.to_dict() != state_model.to_dict():
            raise ValueError
        intent, intent_sha = predecessor._read_record(
            predecessor.OBSERVATION_RECEIPTS_ROOT / f"{request['run_id']}.intent.json"
        )
        receipt, receipt_sha = predecessor._read_record(
            predecessor.OBSERVATION_RECEIPTS_ROOT / f"{request['run_id']}.json"
        )
        predecessor._validate_predecessors_from_intent(
            intent,
            request,
            run,
            contents,
            predecessor_state_model,
        )
        estimate = intent.get("estimated_adjudication_cost_microusd")
        if type(estimate) is not int or intent.get("observed_part_number") != state.get(
            "adjudication_part_count"
        ):
            raise ValueError
        result = predecessor._aggregate(request, state, "observed", estimate)
        predecessor._validate_final_receipt(
            receipt,
            intent=intent,
            result=result,
            contents=contents,
        )
        return intent_sha, receipt_sha
    except Exception as error:
        raise AdjudicationResultProcessingError("processing predecessor invalid") from error


def _worker_env(request: Mapping[str, object], snapshot: RunSnapshot) -> dict[str, str]:
    digests = _digest(snapshot)
    return {
        contract.ENV_ARCHIVE_SHA256: str(request["archive_sha256"]),
        contract.ENV_RUN_ID: str(request["run_id"]),
        contract.ENV_AUTHORIZATION_ID: str(request["authorization_id"]),
        contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: str(
            request["maximum_authorized_cost_microusd"]
        ),
        contract.ENV_EXPECTED_STATE_DIGEST: digests["state"],
        contract.ENV_EXPECTED_ARTIFACTS_DIGEST: digests["artifacts"],
        contract.ENV_EXPECTED_REQUESTS_DIGEST: digests["requests"],
        contract.ENV_EXPECTED_JOURNALS_DIGEST: digests["journals"],
        contract.ENV_EXPECTED_OUTPUTS_DIGEST: digests["outputs"],
        contract.ENV_EXPECTED_DERIVED_DIGEST: digests["derived"],
    }


def _worker_args(
    request: Mapping[str, object],
    source: Path,
    run: Path,
    snapshot: RunSnapshot,
) -> list[str]:
    arguments = [
        "docker",
        "compose",
        "--progress",
        "quiet",
        "--env-file",
        os.fspath(ENV_FILE),
        "--profile",
        host.PROCESSING_COMPOSE_PROFILE,
        "-f",
        os.fspath(COMPOSE_PATH),
        "run",
        "--name",
        host.PROCESSING_CONTAINER_NAME,
        "--rm",
        "--no-TTY",
        "--no-deps",
        "--pull",
        "never",
        "--user",
        f"{host.UID_IN_CONTAINER}:{host.GID_IN_CONTAINER}",
    ]
    for name, value in sorted(_worker_env(request, snapshot).items()):
        arguments.extend(("--env", f"{name}={value}"))
    arguments.extend(
        (
            "--volume",
            f"{source.as_posix()}:/review-workspace:ro",
            "--volume",
            f"{run.as_posix()}:/review-workspace/review-runs/{request['run_id']}:rw",
            host.PROCESSING_COMPOSE_SERVICE,
        )
    )
    return arguments


def _read_bounded(stream: BinaryIO) -> bytes:
    try:
        return stream.read(contract.OUTPUT_MAX_BYTES + 1)
    finally:
        stream.close()


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix" and hasattr(os, "killpg"):
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=WORKER_KILL_AFTER_SECONDS)
            return
        except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
    try:
        process.kill()
    except OSError:
        pass
    process.wait()


def _run_worker(
    request: Mapping[str, object],
    source: Path,
    run: Path,
    snapshot: RunSnapshot,
) -> dict[str, object]:
    arguments = _worker_args(request, source, run, snapshot)
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            arguments,
            cwd=_ROOT,
            env={
                "PATH": "/usr/sbin:/usr/bin",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_SYSTEM": "/dev/null",
                "GIT_TERMINAL_PROMPT": "0",
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=True,
        )
        if process.stdout is None or process.stderr is None:
            raise OSError
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            stdout = pool.submit(_read_bounded, process.stdout)
            stderr = pool.submit(_read_bounded, process.stderr)
            try:
                code = process.wait(timeout=WORKER_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as error:
                _terminate(process)
                raise AdjudicationResultProcessingError("processing worker failed") from error
            output, error_output = stdout.result(timeout=5), stderr.result(timeout=5)
        if code != 0 or error_output or len(output) > contract.OUTPUT_MAX_BYTES:
            raise AdjudicationResultProcessingError("processing worker rejected")
        return contract.parse_aggregate(output)
    except AdjudicationResultProcessingError:
        raise
    except (OSError, subprocess.SubprocessError, TimeoutError, ValueError) as error:
        raise AdjudicationResultProcessingError("processing worker failed") from error
    finally:
        if process is not None and process.poll() is None:
            _terminate(process)


def _state_binding(state: Mapping[str, object]) -> str:
    return _sha(
        _canonical({key: value for key, value in state.items() if key not in _STATE_MUTABLE_FIELDS})
    )


def _validate_pre_state(
    state: Mapping[str, object],
    request: Mapping[str, object],
    preparation: Mapping[str, object],
) -> None:
    try:
        result = preparation["result"]
        state_maximum = _micros(state["maximum_cost_usd"])
        primary_cost = _micros(state["actual_primary_cost_usd"])
        adjudication_cost = _micros(state["actual_adjudication_cost_usd"])
        if (
            not isinstance(result, dict)
            or state.get("status") != "adjudication_part_completed"
            or state.get("run_id") != request["run_id"]
            or state.get("candidate_count") != result.get("candidate_count")
            or state.get("primary_part_count") != result.get("primary_part_count")
            or type(state.get("primary_part_count")) is not int
            or state["primary_part_count"] <= 0
            or state.get("primary_completed_part_count") != state["primary_part_count"]
            or type(state.get("adjudication_part_count")) is not int
            or state["adjudication_part_count"] <= 0
            or state.get("adjudication_completed_part_count") != state["adjudication_part_count"]
            or state.get("accepted_by_adjudication") != 0
            or state.get("needs_human") != 0
            or state.get("final_review_part_count") != 0
            or state.get("final_review_completed_part_count") != 0
            or state.get("actual_final_review_cost_usd") != 0.0
            or state.get("final_review_batch_id") is not None
            or state.get("final_review_input_file_id") is not None
            or bool(state.get("final_review_batch_ids"))
            or bool(state.get("final_review_input_file_ids"))
            or request["maximum_authorized_cost_microusd"] > state_maximum
            or primary_cost + adjudication_cost > request["maximum_authorized_cost_microusd"]
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise AdjudicationResultProcessingError("processing checkpoint invalid") from error


def _aggregate(
    request: Mapping[str, object],
    state: Mapping[str, object],
    *,
    status: str,
) -> dict[str, object]:
    try:
        primary_cost = _micros(state["actual_primary_cost_usd"])
        adjudication_cost = _micros(state["actual_adjudication_cost_usd"])
        state_maximum = _micros(state["maximum_cost_usd"])
        if (
            state.get("status") not in contract.RUN_STATUSES
            or state.get("run_id") != request["run_id"]
            or request["maximum_authorized_cost_microusd"] > state_maximum
            or primary_cost + adjudication_cost > request["maximum_authorized_cost_microusd"]
            or state.get("actual_final_review_cost_usd") != 0.0
            or state.get("final_review_completed_part_count") != 0
            or state.get("final_review_batch_id") is not None
            or state.get("final_review_input_file_id") is not None
            or bool(state.get("final_review_batch_ids"))
            or bool(state.get("final_review_input_file_ids"))
        ):
            raise ValueError
        value = {
            "accepted_by_consensus": state["accepted_by_consensus"],
            "accepted_by_adjudication": state["accepted_by_adjudication"],
            "actual_adjudication_cost_microusd": adjudication_cost,
            "actual_primary_cost_microusd": primary_cost,
            "adjudication_completed_part_count": state["adjudication_completed_part_count"],
            "adjudication_part_count": state["adjudication_part_count"],
            "candidate_count": state["candidate_count"],
            "final_review_part_count": state["final_review_part_count"],
            "maximum_authorized_cost_microusd": request["maximum_authorized_cost_microusd"],
            "needs_human": state["needs_human"],
            "operation": contract.OPERATION,
            "primary_completed_part_count": state["primary_completed_part_count"],
            "primary_part_count": state["primary_part_count"],
            "purpose": contract.PURPOSE,
            "run_status": state["status"],
            "season_number": contract.SEASON_NUMBER,
            "status": status,
        }
        return contract.validate_aggregate(value, status=status)
    except (KeyError, TypeError, ValueError) as error:
        raise AdjudicationResultProcessingError("processing aggregate invalid") from error


def _directory_payload(snapshot: RunSnapshot) -> dict[str, object]:
    if snapshot.directory_identity is None:
        raise AdjudicationResultProcessingError("processing inventory invalid")
    return {
        "children": {
            name: list(identity) for name, identity in sorted(snapshot.directories.items())
        },
        "root": list(snapshot.directory_identity),
    }


def _claim_payload(request: Mapping[str, object], authorization_sha: str) -> dict[str, object]:
    return {
        "archive_sha256": request["archive_sha256"],
        "authorization_id": request["authorization_id"],
        "authorization_sha256": authorization_sha,
        "maximum_authorized_cost_microusd": request["maximum_authorized_cost_microusd"],
        "operation": contract.OPERATION,
        "request_sha256": _sha(_canonical(request)),
        "run_id": request["run_id"],
        "schema_version": contract.PROTOCOL_VERSION,
        "status": "claimed",
    }


def _intent_payload(
    request: Mapping[str, object],
    authorization_sha: str,
    preparation: Mapping[str, object],
    preparation_sha: str,
    predecessor_intent_sha: str,
    predecessor_receipt_sha: str,
    snapshot: RunSnapshot,
    state: Mapping[str, object],
) -> dict[str, object]:
    try:
        manifest_sha = _sha(snapshot.artifacts["source-manifest.json"])
        return {
            "archive_sha256": request["archive_sha256"],
            "authorization_id": request["authorization_id"],
            "authorization_sha256": authorization_sha,
            "configuration_sha256": preparation["config_sha"],
            "fourth_observation_intent_sha256": predecessor_intent_sha,
            "fourth_observation_receipt_sha256": predecessor_receipt_sha,
            "image_reference": preparation["image"],
            "maximum_authorized_cost_microusd": request["maximum_authorized_cost_microusd"],
            "operation": contract.OPERATION,
            "pre_digests": _digest(snapshot),
            "pre_directory_identities": _directory_payload(snapshot),
            "pre_hashes": _hashes(snapshot),
            "pre_state_binding_sha256": _state_binding(state),
            "pre_state_sha256": _sha(snapshot.state),
            "preparation_receipt_sha256": preparation_sha,
            "purpose": contract.PURPOSE,
            "release_sha": preparation["release_sha"],
            "request_sha256": _sha(_canonical(request)),
            "run_id": request["run_id"],
            "schema_version": contract.PROTOCOL_VERSION,
            "season_number": contract.SEASON_NUMBER,
            "source_manifest_sha256": manifest_sha,
            "status": "intent",
        }
    except (KeyError, TypeError) as error:
        raise AdjudicationResultProcessingError("processing intent invalid") from error


def _validate_intent_base(
    value: object,
    *,
    request: Mapping[str, object],
    authorization_sha: str,
    preparation: Mapping[str, object],
    preparation_sha: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _INTENT_KEYS:
        raise AdjudicationResultProcessingError("processing intent invalid")
    fixed = {
        "archive_sha256": request["archive_sha256"],
        "authorization_id": request["authorization_id"],
        "authorization_sha256": authorization_sha,
        "configuration_sha256": preparation["config_sha"],
        "image_reference": preparation["image"],
        "maximum_authorized_cost_microusd": request["maximum_authorized_cost_microusd"],
        "operation": contract.OPERATION,
        "preparation_receipt_sha256": preparation_sha,
        "purpose": contract.PURPOSE,
        "release_sha": preparation["release_sha"],
        "request_sha256": _sha(_canonical(request)),
        "run_id": request["run_id"],
        "schema_version": contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
        "status": "intent",
    }
    if any(value.get(key) != item for key, item in fixed.items()):
        raise AdjudicationResultProcessingError("processing intent binding changed")
    for key in (
        "fourth_observation_intent_sha256",
        "fourth_observation_receipt_sha256",
        "pre_state_binding_sha256",
        "pre_state_sha256",
        "source_manifest_sha256",
    ):
        item = value.get(key)
        if (
            not isinstance(item, str)
            or len(item) != 64
            or any(character not in "0123456789abcdef" for character in item)
        ):
            raise AdjudicationResultProcessingError("processing intent invalid")
    if not all(
        isinstance(value.get(key), dict)
        for key in ("pre_digests", "pre_directory_identities", "pre_hashes")
    ):
        raise AdjudicationResultProcessingError("processing intent invalid")
    return dict(value)


def _validate_predecessor_record_hashes(
    request: Mapping[str, object], intent: Mapping[str, object]
) -> None:
    try:
        _, observed_intent_sha = predecessor._read_record(
            predecessor.OBSERVATION_RECEIPTS_ROOT / f"{request['run_id']}.intent.json"
        )
        _, observed_receipt_sha = predecessor._read_record(
            predecessor.OBSERVATION_RECEIPTS_ROOT / f"{request['run_id']}.json"
        )
        if (
            intent.get("fourth_observation_intent_sha256") != observed_intent_sha
            or intent.get("fourth_observation_receipt_sha256") != observed_receipt_sha
        ):
            raise ValueError
    except Exception as error:
        raise AdjudicationResultProcessingError(
            "processing predecessor evidence changed"
        ) from error


def _mapping_hashes(value: object) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict) or set(value) != {
        "artifacts",
        "requests",
        "journals",
        "outputs",
        "derived",
    }:
        raise AdjudicationResultProcessingError("processing intent invalid")
    result: dict[str, dict[str, str]] = {}
    for group, contents in value.items():
        if not isinstance(group, str) or not isinstance(contents, dict):
            raise AdjudicationResultProcessingError("processing intent invalid")
        checked: dict[str, str] = {}
        for name, digest in contents.items():
            if (
                not isinstance(name, str)
                or not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise AdjudicationResultProcessingError("processing intent invalid")
            checked[name] = digest
        result[group] = checked
    return result


def _identity_payload(value: object) -> tuple[list[int], dict[str, list[int]]]:
    if not isinstance(value, dict) or set(value) != {"children", "root"}:
        raise AdjudicationResultProcessingError("processing intent invalid")
    root = value.get("root")
    children = value.get("children")
    if (
        not isinstance(root, list)
        or len(root) != 5
        or not all(type(item) is int and item >= 0 for item in root)
        or not isinstance(children, dict)
    ):
        raise AdjudicationResultProcessingError("processing intent invalid")
    checked: dict[str, list[int]] = {}
    for name, identity in children.items():
        if (
            not isinstance(name, str)
            or not isinstance(identity, list)
            or len(identity) != 5
            or not all(type(item) is int and item >= 0 for item in identity)
        ):
            raise AdjudicationResultProcessingError("processing intent invalid")
        checked[name] = identity
    return root, checked


def _validate_recovery(
    snapshot: RunSnapshot,
    state: Mapping[str, object],
    intent: Mapping[str, object],
) -> None:
    hashes = _mapping_hashes(intent.get("pre_hashes"))
    digests = intent.get("pre_digests")
    if not isinstance(digests, dict) or set(digests) != {
        "state",
        "artifacts",
        "requests",
        "journals",
        "outputs",
        "derived",
    }:
        raise AdjudicationResultProcessingError("processing intent invalid")
    current = {
        "artifacts": snapshot.artifacts,
        "requests": snapshot.requests,
        "journals": snapshot.journals,
        "outputs": snapshot.outputs,
        "derived": snapshot.derived,
    }
    for group, expected_hashes in hashes.items():
        observed = current[group]
        if group in {"artifacts", "journals", "outputs"} and set(observed) != set(expected_hashes):
            raise AdjudicationResultProcessingError("processing evidence changed")
        if not set(expected_hashes) <= set(observed) or any(
            _sha(observed[name]) != digest for name, digest in expected_hashes.items()
        ):
            raise AdjudicationResultProcessingError("processing evidence changed")
        original = {name: observed[name] for name in expected_hashes}
        if worker._digest(original) != digests.get(group):
            raise AdjudicationResultProcessingError("processing evidence changed")
    try:
        manifest_sha = _sha(snapshot.artifacts["source-manifest.json"])
    except KeyError as error:
        raise AdjudicationResultProcessingError("processing evidence changed") from error
    if (
        digests.get("state") != intent.get("pre_state_sha256")
        or _state_binding(state) != intent.get("pre_state_binding_sha256")
        or manifest_sha != intent.get("source_manifest_sha256")
    ):
        raise AdjudicationResultProcessingError("processing evidence changed")
    root, children = _identity_payload(intent.get("pre_directory_identities"))
    if snapshot.directory_identity is None:
        raise AdjudicationResultProcessingError("processing evidence changed")
    created = set(snapshot.directories) - set(children)
    for name, identity in children.items():
        observed = snapshot.directories.get(name)
        if observed is None or (
            observed[0],
            observed[1],
            observed[4],
        ) != (
            identity[0],
            identity[1],
            worker._expected_directory_link_count(tuple(identity), name, created),
        ):
            raise AdjudicationResultProcessingError("processing evidence changed")
    if (
        snapshot.directory_identity[0],
        snapshot.directory_identity[1],
        snapshot.directory_identity[4],
    ) != (
        root[0],
        root[1],
        worker._expected_directory_link_count(tuple(root), ".", created),
    ):
        raise AdjudicationResultProcessingError("processing evidence changed")
    if state.get("status") == "adjudication_part_completed":
        if _sha(snapshot.state) != intent.get("pre_state_sha256"):
            raise AdjudicationResultProcessingError("processing evidence changed")
    elif state.get("status") not in contract.RUN_STATUSES:
        raise AdjudicationResultProcessingError("processing checkpoint invalid")


def _validate_transition(before: RunSnapshot, after: RunSnapshot) -> None:
    if (
        before.artifacts != after.artifacts
        or before.journals != after.journals
        or before.outputs != after.outputs
        or any(after.requests.get(name) != raw for name, raw in before.requests.items())
        or any(after.derived.get(name) != raw for name, raw in before.derived.items())
    ):
        raise AdjudicationResultProcessingError("processing evidence changed")


def _receipt_payload(
    request: Mapping[str, object],
    claim_sha: str,
    intent_sha: str,
    snapshot: RunSnapshot,
    aggregate: Mapping[str, object],
) -> dict[str, object]:
    return {
        "aggregate": dict(aggregate),
        "archive_sha256": request["archive_sha256"],
        "authorization_claim_sha256": claim_sha,
        "authorization_id": request["authorization_id"],
        "intent_sha256": intent_sha,
        "operation": contract.OPERATION,
        "post_counts": {
            "artifacts": len(snapshot.artifacts),
            "derived": len(snapshot.derived),
            "journals": len(snapshot.journals),
            "outputs": len(snapshot.outputs),
            "requests": len(snapshot.requests),
        },
        "post_digests": _digest(snapshot),
        "purpose": contract.PURPOSE,
        "run_id": request["run_id"],
        "schema_version": contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
        "status": "receipt",
    }


def _validate_runtime(preparation: Mapping[str, object]) -> None:
    try:
        if runtime._active_binding() != (
            preparation["release_sha"],
            preparation["image"],
            preparation["config_sha"],
        ):
            raise ValueError
    except Exception as error:
        raise AdjudicationResultProcessingError("active runtime changed") from error


def process_request(request: Mapping[str, object]) -> dict[str, object]:
    try:
        request = contract.validate_request(request)
    except (TypeError, ValueError) as error:
        raise AdjudicationResultProcessingError("invalid processing request") from error
    authorization_sha = _authorization(request)
    source, _runs, run, preparation, preparation_sha = _run_paths(request)
    _directory(RECEIPTS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    receipt_path = RECEIPTS_ROOT / f"{request['run_id']}.json"
    intent_path = RECEIPTS_ROOT / f"{request['run_id']}.intent.json"
    claim_path = RECEIPTS_ROOT / f"authorization-{request['authorization_id']}.claim.json"
    snapshot = _inventory(run)
    state, state_model = _state_model(run, snapshot)
    intent_exists = os.path.lexists(intent_path)
    receipt_exists = os.path.lexists(receipt_path)
    claim_exists = os.path.lexists(claim_path)
    for path, published in (
        (claim_path, claim_exists),
        (intent_path, intent_exists),
        (receipt_path, receipt_exists),
    ):
        pending = path.with_name(f".{path.name}.pending")
        if os.path.lexists(pending) and not published:
            raise AdjudicationResultProcessingError("processing receipt state ambiguous")
    if receipt_exists and not intent_exists:
        raise AdjudicationResultProcessingError("orphan processing receipt")
    if intent_exists and not claim_exists:
        raise AdjudicationResultProcessingError("orphan processing intent")

    claim = _claim_payload(request, authorization_sha)
    if not intent_exists:
        if receipt_exists:
            raise AdjudicationResultProcessingError("orphan processing receipt")
        _validate_pre_state(state, request, preparation)
        predecessor_intent_sha, predecessor_receipt_sha = _validate_predecessors(
            request,
            run,
            state_model,
        )
        claim_sha = _write_once(claim_path, claim)
        intent = _intent_payload(
            request,
            authorization_sha,
            preparation,
            preparation_sha,
            predecessor_intent_sha,
            predecessor_receipt_sha,
            snapshot,
            state,
        )
        intent_sha = _write_once(intent_path, intent)
    else:
        stored_claim, claim_sha = _read_root_record(claim_path)
        if stored_claim != claim:
            raise AdjudicationResultProcessingError("processing authorization conflict")
        stored_intent, intent_sha = _read_root_record(intent_path)
        intent = _validate_intent_base(
            stored_intent,
            request=request,
            authorization_sha=authorization_sha,
            preparation=preparation,
            preparation_sha=preparation_sha,
        )
        _validate_predecessor_record_hashes(request, intent)
        _validate_recovery(snapshot, state, intent)

    if receipt_exists:
        aggregate = _aggregate(request, state, status=str(state["status"]))
        stored_receipt, _ = _read_root_record(receipt_path)
        expected_receipt = _receipt_payload(
            request,
            claim_sha,
            intent_sha,
            snapshot,
            aggregate,
        )
        if stored_receipt != expected_receipt:
            raise AdjudicationResultProcessingError("processing receipt conflict")
        return _aggregate(request, state, status="already_processed")

    if state.get("status") in contract.RUN_STATUSES:
        worker_result = _run_worker(request, source, run, snapshot)
        after = _inventory(run)
        after_state, _ = _state_model(run, after)
        _validate_recovery(after, after_state, intent)
        expected_replay = _aggregate(request, after_state, status="already_processed")
        if worker_result != expected_replay or after != snapshot:
            raise AdjudicationResultProcessingError("processing recovery result invalid")
        aggregate = _aggregate(
            request,
            after_state,
            status=str(after_state["status"]),
        )
        _validate_runtime(preparation)
        primary_processing._source_workspace(request)
        _write_once(
            receipt_path,
            _receipt_payload(request, claim_sha, intent_sha, after, aggregate),
        )
        return aggregate

    _validate_pre_state(state, request, preparation)
    worker_result = _run_worker(request, source, run, snapshot)
    after = _inventory(run)
    after_state, _ = _state_model(run, after)
    _validate_transition(snapshot, after)
    _validate_recovery(after, after_state, intent)
    aggregate = _aggregate(request, after_state, status=str(after_state["status"]))
    if worker_result != aggregate:
        raise AdjudicationResultProcessingError("processing worker result invalid")
    _validate_runtime(preparation)
    primary_processing._source_workspace(request)
    _write_once(
        receipt_path,
        _receipt_payload(request, claim_sha, intent_sha, after, aggregate),
    )
    return aggregate


def _require_root() -> None:
    if (
        os.name != "posix"
        or not hasattr(os, "geteuid")
        or os.geteuid() != ROOT_UID
        or os.environ.get("SUDO_USER") != host.REVIEW_USER
        or platform.system() != "Linux"
        or platform.machine() != "x86_64"
    ):
        raise AdjudicationResultProcessingError("invalid processing caller")


def main() -> int:
    try:
        _require_root()
        sys.stdout.buffer.write(
            contract.canonical_json(process_request(_read_request(sys.stdin.buffer)))
        )
        return 0
    except Exception:
        sys.stderr.write("error=speaker_review_adjudication_result_processing_rejected\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
