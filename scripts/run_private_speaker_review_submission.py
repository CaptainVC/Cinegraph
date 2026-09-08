"""Root-only coordinator for one paid primary speaker-review submission.

This file is intentionally standard-library-only.  It is executed by the
root-owned review helper with ``python3 -I -S`` and is the host-side authority
for the transition from Phase 60 preparation to one provider submission.

The coordinator never receives a corpus path or a provider secret.  It reads
only root-controlled authorization and preparation receipts, mounts the
prepared ``review-runs`` directory into the dedicated Compose worker, and
returns the small aggregate defined by
``private_speaker_review_submission_contract``.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import os
import platform
import re
import signal
import stat
import subprocess
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Final, Mapping

# Isolated mode removes the script directory from import search paths on some
# Python builds.  Re-add only this release's own scripts directory; no site
# packages or caller-controlled PYTHONPATH is accepted.
_SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if os.fspath(_SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, os.fspath(_SCRIPT_DIRECTORY))

try:
    # Normal repository imports used by tests and by an installed release.
    from scripts import private_speaker_review_submission_contract as contract
except ModuleNotFoundError:
    # ``python3 -I -S scripts/<this-file>`` has the scripts directory, but not
    # the repository parent, on sys.path.  The contract itself is stdlib-only.
    import private_speaker_review_submission_contract as contract


class SpeakerReviewSubmissionProcessingError(RuntimeError):
    """A path-free, provider-free root-coordinator rejection."""


# These paths deliberately mirror the Phase 60 host contract without importing
# it: the root entrypoint must remain usable with ``-I -S`` even when only the
# two submission contract files have been copied into a release.
RELEASE_ROOT: Path = Path(__file__).resolve().parents[1]
SPEAKER_REVIEW_ROOT: Path = Path("/opt/cinegraph/shared/private-corpus/dev/speaker-review")
PREPARATION_RECEIPTS_ROOT: Path = SPEAKER_REVIEW_ROOT / "receipts"
REVIEW_RUNS_ROOT: Path = SPEAKER_REVIEW_ROOT.parent / "review-runs"
AUTHORIZATION_ROOT: Path = SPEAKER_REVIEW_ROOT / "authorization"
SUBMISSION_RECEIPTS_ROOT: Path = SPEAKER_REVIEW_ROOT / "submission-receipts"
DEV_ENV_FILE: Path = Path("/etc/cinegraph/dev.env")

COMPOSE_PROFILE: Final = "corpus-speaker-review-submit-primary"
COMPOSE_SERVICE: Final = "corpus-speaker-review-submit-primary"
CONTAINER_NAME: Final = "cinegraph-speaker-review-submit-primary"
COMPOSE_PATH: Path = RELEASE_ROOT / "deploy/compose.yaml"
WORKER_MOUNT: Final = "/review-workspace/review-runs"
WORKER_UID: Final = 10002
WORKER_GID: Final = 10002
ROOT_UID: Final = 0
ROOT_GID: Final = 0

RECEIPT_SCHEMA_VERSION: Final = 1
ROOT_RECORD_MAX_BYTES: Final = 128 * 1024
RUN_STATE_MAX_BYTES: Final = 64 * 1024
RUN_ARTIFACT_MAX_BYTES: Final = 64 * 1024 * 1024
RUN_ARTIFACT_TOTAL_MAX_BYTES: Final = 256 * 1024 * 1024
WORKER_SHUTDOWN_SECONDS: Final = 60
WORKER_TIMEOUT_SECONDS: Final = 1_800 - WORKER_SHUTDOWN_SECONDS
WORKER_KILL_AFTER_SECONDS: Final = 10
WORKER_OUTPUT_MAX_BYTES: Final = contract.OUTPUT_MAX_BYTES

_SHA256 = set("0123456789abcdef")
_RUN_STATE_FILENAME: Final = "run-state.json"
_CANDIDATES_FILENAME: Final = "candidates.jsonl"
_SOURCE_MANIFEST_FILENAME: Final = "source-manifest.json"
_OBJECT_DIRECTORY_PREFIX: Final = "sha256-"
_RUN_DIRECTORY_NAME: Final = "review-runs"
_PRIMARY_REQUEST_FILENAME_TEMPLATE: Final = "primary-part-{part_number:04d}-requests.jsonl"
_WORKFLOW_INTENT_FILENAME: Final = ".primary-part-0001-submission-intent.json"
_WORKFLOW_COMPLETED_FILENAME: Final = ".primary-part-0001-submission-completed.json"
_CONFIGURATION_BINDING_FILES: Final = (
    "src/cinegraph/common/prompts.py",
    "src/cinegraph/config/models.py",
    "src/cinegraph/config/speaker_review.py",
    "src/cinegraph/config/speaker_review_filesystem.py",
    "src/cinegraph/ingestion/speaker_review/batch_requests.py",
    "src/cinegraph/ingestion/speaker_review/costs.py",
)
_IMAGE_BINDING_KEYS: Final = frozenset(
    {
        "CINEGRAPH_ENVIRONMENT",
        "CINEGRAPH_IMAGE",
        "CINEGRAPH_IMAGE_DIGEST",
        "CINEGRAPH_RELEASE_SHA",
    }
)
_IMAGE_NAME: Final = "ghcr.io/captainvc/cinegraph"
_ENVIRONMENT_NAME: Final = "development"
_RELEASE_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_IMAGE_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

_PREPARATION_RECEIPT_KEYS: Final = frozenset(
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
_PREPARATION_RESULT_KEYS: Final = frozenset(
    {
        "candidate_count",
        "estimated_primary_cost_usd",
        "file_count",
        "operation",
        "primary_part_count",
        "purpose",
        "run_id",
        "season_number",
        "status",
        "total_bytes",
    }
)

# This is the exact JSON shape emitted by SpeakerReviewRunState.to_dict().
# Keeping the shape here lets the root boundary validate post-submit state
# without importing pydantic, LangGraph, or any application dependency.
_RUN_STATE_KEYS: Final = frozenset(
    {
        "schema_version",
        "run_id",
        "status",
        "created_at",
        "updated_at",
        "candidate_count",
        "primary_model",
        "adjudication_model",
        "prompt_version",
        "maximum_cost_usd",
        "estimated_primary_cost_usd",
        "actual_primary_cost_usd",
        "actual_adjudication_cost_usd",
        "final_review_model",
        "actual_final_review_cost_usd",
        "primary_batch_id",
        "primary_input_file_id",
        "adjudication_batch_id",
        "adjudication_input_file_id",
        "primary_part_count",
        "primary_completed_part_count",
        "primary_batch_ids",
        "primary_input_file_ids",
        "adjudication_part_count",
        "adjudication_completed_part_count",
        "adjudication_batch_ids",
        "adjudication_input_file_ids",
        "final_review_part_count",
        "final_review_completed_part_count",
        "final_review_batch_ids",
        "final_review_input_file_ids",
        "final_review_batch_id",
        "final_review_input_file_id",
        "final_review_retry_count",
        "accepted_by_consensus",
        "accepted_by_adjudication",
        "accepted_by_final_review",
        "accepted_by_human",
        "needs_human",
        "actual_total_cost_usd",
    }
)

_WORKFLOW_BINDING_KEYS: Final = frozenset(
    {
        "schema_version",
        "request_sha256",
        "run_id",
        "stage",
        "part",
        "prompt_version",
        "batch_endpoint",
        "completion_window",
    }
)

_ROOT_INTENT_KEYS: Final = frozenset(
    {
        "archive_sha256",
        "authorization_id",
        "authorization_sha256",
        "estimated_primary_cost_microusd",
        "maximum_authorized_cost_microusd",
        "operation",
        "prep_receipt_sha256",
        "prepared_artifact_file_count",
        "prepared_artifact_set_sha256",
        "prepared_artifact_hashes",
        "primary_part_count",
        "purpose",
        "run_id",
        "schema_version",
        "season_number",
        "status",
    }
)

_ROOT_RECEIPT_KEYS: Final = frozenset(
    {
        *_ROOT_INTENT_KEYS,
        "post_artifact_file_count",
        "post_artifact_set_sha256",
        "post_journal_file_count",
        "post_journal_set_sha256",
        "post_run_state_sha256",
        "result",
    }
)


@dataclass(frozen=True, slots=True)
class PreparationBinding:
    receipt: dict[str, object]
    receipt_sha256: str
    result: dict[str, object]
    artifact_hashes: dict[str, str]
    artifact_file_count: int
    artifact_set_sha256: str


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    run_directory: Path
    state: dict[str, object]
    base_contents: dict[str, bytes]
    all_contents: dict[str, bytes]
    base_hashes: dict[str, str]
    base_set_sha256: str
    all_set_sha256: str
    journal_set_sha256: str
    journal_names: tuple[str, ...]
    journal_phase: str


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _decode_json(raw: bytes, *, maximum: int, canonical: bool) -> dict[str, object]:
    if (
        not isinstance(raw, bytes)
        or not raw
        or len(raw) > maximum
        or not raw.endswith(b"\n")
        or raw.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"))
    ):
        raise ValueError("invalid JSON")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("invalid JSON") from error
    if not isinstance(value, dict):
        raise ValueError("invalid JSON")
    if canonical and _canonical_json(value) != raw:
        raise ValueError("noncanonical JSON")
    return value


def _stable_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_nlink,
    )


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


def _owner_matches(metadata: os.stat_result, uid: int, gid: int) -> bool:
    if os.name != "posix":
        return True
    return metadata.st_uid == uid and metadata.st_gid == gid


def _regular_metadata(
    path: Path,
    *,
    maximum: int,
    mode: int | None,
    owner: tuple[int, int] | None,
) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise SpeakerReviewSubmissionProcessingError("submission evidence unavailable") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
        or metadata.st_nlink != 1
        or metadata.st_size <= 0
        or metadata.st_size > maximum
        or (mode is not None and os.name == "posix" and stat.S_IMODE(metadata.st_mode) != mode)
        or (owner is not None and not _owner_matches(metadata, *owner))
    ):
        raise SpeakerReviewSubmissionProcessingError("submission evidence unavailable")
    return metadata


def _read_stable_file(
    path: Path,
    *,
    maximum: int,
    mode: int | None,
    owner: tuple[int, int] | None,
    canonical: bool = False,
) -> tuple[bytes, os.stat_result]:
    descriptor = -1
    try:
        before = _regular_metadata(path, maximum=maximum, mode=mode, owner=owner)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or stat.S_ISLNK(opened.st_mode)
            or _is_reparse(opened)
            or not _owner_matches(opened, *(owner or (opened.st_uid, opened.st_gid)))
            or _stable_identity(opened) != _stable_identity(before)
        ):
            raise OSError
        raw = os.read(descriptor, maximum + 1)
        after = path.lstat()
    except SpeakerReviewSubmissionProcessingError:
        raise
    except OSError as error:
        raise SpeakerReviewSubmissionProcessingError("submission evidence unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        _stable_identity(after) != _stable_identity(before)
        or len(raw) != opened.st_size
        or len(raw) > maximum
    ):
        raise SpeakerReviewSubmissionProcessingError("submission evidence changed")
    if canonical:
        try:
            _decode_json(raw, maximum=maximum, canonical=True)
        except ValueError as error:
            raise SpeakerReviewSubmissionProcessingError("submission evidence invalid") from error
    return raw, before


def _validate_directory(
    path: Path,
    *,
    mode: int,
    owner: tuple[int, int] | None,
) -> None:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise SpeakerReviewSubmissionProcessingError("submission evidence unavailable") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
        or (os.name == "posix" and stat.S_IMODE(metadata.st_mode) != mode)
        or (owner is not None and not _owner_matches(metadata, *owner))
        or resolved != path
    ):
        raise SpeakerReviewSubmissionProcessingError("submission evidence unavailable")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _SHA256


def _is_release_sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 40 and set(value) <= _SHA256


def _safe_text(value: object, *, maximum: int = 512) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and value == value.strip()
        and not any(ord(character) < 32 for character in value)
    )


def _cost_microusd(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpeakerReviewSubmissionProcessingError("submission cost invalid")
    if not math.isfinite(float(value)) or float(value) < 0:
        raise SpeakerReviewSubmissionProcessingError("submission cost invalid")
    try:
        micros = Decimal(str(value)) * Decimal(1_000_000)
    except (InvalidOperation, ValueError) as error:
        raise SpeakerReviewSubmissionProcessingError("submission cost invalid") from error
    if micros != micros.to_integral_value() or micros < 0:
        raise SpeakerReviewSubmissionProcessingError("submission cost invalid")
    result = int(micros)
    if result > contract.MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise SpeakerReviewSubmissionProcessingError("submission cost invalid")
    return result


def _configuration_sha256(release: Path) -> str:
    """Match Phase 60's configuration binding without importing the app."""

    digest = hashlib.sha256()
    try:
        for locator in _CONFIGURATION_BINDING_FILES:
            path = release.joinpath(*PurePosixPath(locator).parts)
            metadata = path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_size > RUN_STATE_MAX_BYTES
            ):
                raise SpeakerReviewSubmissionProcessingError("configuration unavailable")
            content = path.read_bytes()
            if len(content) != metadata.st_size:
                raise SpeakerReviewSubmissionProcessingError("configuration unavailable")
            encoded_locator = locator.encode("ascii")
            digest.update(len(encoded_locator).to_bytes(4, "big"))
            digest.update(encoded_locator)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
    except SpeakerReviewSubmissionProcessingError:
        raise
    except (OSError, UnicodeError, ValueError) as error:
        raise SpeakerReviewSubmissionProcessingError("configuration unavailable") from error
    return digest.hexdigest()


def _release_image_reference() -> str:
    """Parse the exact Phase 60 image binding from the root env file."""

    raw, _ = _read_stable_file(
        DEV_ENV_FILE,
        maximum=64 * 1024,
        mode=0o600,
        owner=(ROOT_UID, ROOT_GID),
    )
    values: dict[str, str] = {}
    try:
        for line in raw.decode("utf-8").splitlines():
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if separator and key in _IMAGE_BINDING_KEYS:
                if key in values:
                    raise ValueError("duplicate binding")
                values[key] = value
    except (UnicodeError, ValueError) as error:
        raise SpeakerReviewSubmissionProcessingError("release image unavailable") from error
    release_sha = values.get("CINEGRAPH_RELEASE_SHA", "")
    if (
        set(values) != _IMAGE_BINDING_KEYS
        or values.get("CINEGRAPH_ENVIRONMENT") != _ENVIRONMENT_NAME
        or _RELEASE_SHA_PATTERN.fullmatch(release_sha) is None
        or release_sha != RELEASE_ROOT.name
        or values.get("CINEGRAPH_IMAGE") != _IMAGE_NAME
        or _IMAGE_DIGEST_PATTERN.fullmatch(values.get("CINEGRAPH_IMAGE_DIGEST", "")) is None
    ):
        raise SpeakerReviewSubmissionProcessingError("release image unavailable")
    return f"{_IMAGE_NAME}@{values['CINEGRAPH_IMAGE_DIGEST']}"


def _active_runtime_binding() -> tuple[str, str, str]:
    release_sha = RELEASE_ROOT.name
    if _RELEASE_SHA_PATTERN.fullmatch(release_sha) is None:
        raise SpeakerReviewSubmissionProcessingError("active release unavailable")
    return release_sha, _release_image_reference(), _configuration_sha256(RELEASE_ROOT)


def _read_request(stream: BinaryIO) -> dict[str, object]:
    raw = stream.readline(contract.REQUEST_MAX_BYTES + 1)
    if stream.read(1):
        raise SpeakerReviewSubmissionProcessingError("invalid submission request")
    try:
        return contract.parse_request(raw)
    except (TypeError, ValueError) as error:
        raise SpeakerReviewSubmissionProcessingError("invalid submission request") from error


def _expected_base_names(part_count: int) -> set[str]:
    if type(part_count) is not int or part_count <= 0 or part_count > 1024:
        raise SpeakerReviewSubmissionProcessingError("prepared run invalid")
    return {
        _CANDIDATES_FILENAME,
        _SOURCE_MANIFEST_FILENAME,
        _RUN_STATE_FILENAME,
        *(
            _PRIMARY_REQUEST_FILENAME_TEMPLATE.format(part_number=part_number)
            for part_number in range(1, part_count + 1)
        ),
    }


def _set_digest(contents: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(contents):
        encoded_name = name.encode("ascii")
        content = contents[name]
        digest.update(len(encoded_name).to_bytes(4, "big"))
        digest.update(encoded_name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _array_of_strings(value: object) -> bool:
    return isinstance(value, list) and all(_safe_text(item, maximum=512) for item in value)


def _optional_identifier(value: object) -> bool:
    return value is None or _safe_text(value, maximum=512)


def _nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0
    )


def _validate_run_state(
    state: object,
    *,
    preparation: PreparationBinding,
    expected_status: str | None = None,
) -> dict[str, object]:
    if not isinstance(state, dict) or set(state) != _RUN_STATE_KEYS:
        raise SpeakerReviewSubmissionProcessingError("run state invalid")
    result = preparation.result
    if (
        type(state.get("schema_version")) is not int
        or state["schema_version"] <= 0
        or state.get("run_id") != result["run_id"]
        or not _safe_text(state.get("status"), maximum=64)
        or (expected_status is not None and state["status"] != expected_status)
        or not _safe_text(state.get("created_at"), maximum=128)
        or not _safe_text(state.get("updated_at"), maximum=128)
        or type(state.get("candidate_count")) is not int
        or state["candidate_count"] != result["candidate_count"]
        or not _safe_text(state.get("primary_model"), maximum=128)
        or not _safe_text(state.get("adjudication_model"), maximum=128)
        or not _safe_text(state.get("prompt_version"), maximum=128)
        or not _finite_number(state.get("maximum_cost_usd"))
        or not _finite_number(state.get("estimated_primary_cost_usd"))
        or _cost_microusd(state["estimated_primary_cost_usd"])
        != _cost_microusd(result["estimated_primary_cost_usd"])
        or not _finite_number(state.get("actual_primary_cost_usd"))
        or not _finite_number(state.get("actual_adjudication_cost_usd"))
        or not isinstance(state.get("final_review_model"), str)
        or len(state["final_review_model"]) > 128
        or any(ord(character) < 32 for character in state["final_review_model"])
        or not _finite_number(state.get("actual_final_review_cost_usd"))
        or not _optional_identifier(state.get("primary_batch_id"))
        or not _optional_identifier(state.get("primary_input_file_id"))
        or not _optional_identifier(state.get("adjudication_batch_id"))
        or not _optional_identifier(state.get("adjudication_input_file_id"))
        or type(state.get("primary_part_count")) is not int
        or state["primary_part_count"] != result["primary_part_count"]
        or not _nonnegative_int(state.get("primary_completed_part_count"))
        or not _array_of_strings(state.get("primary_batch_ids"))
        or not _array_of_strings(state.get("primary_input_file_ids"))
        or not _nonnegative_int(state.get("adjudication_part_count"))
        or not _nonnegative_int(state.get("adjudication_completed_part_count"))
        or not _array_of_strings(state.get("adjudication_batch_ids"))
        or not _array_of_strings(state.get("adjudication_input_file_ids"))
        or not _nonnegative_int(state.get("final_review_part_count"))
        or not _nonnegative_int(state.get("final_review_completed_part_count"))
        or not _array_of_strings(state.get("final_review_batch_ids"))
        or not _array_of_strings(state.get("final_review_input_file_ids"))
        or not _optional_identifier(state.get("final_review_batch_id"))
        or not _optional_identifier(state.get("final_review_input_file_id"))
        or not _nonnegative_int(state.get("final_review_retry_count"))
        or not _nonnegative_int(state.get("accepted_by_consensus"))
        or not _nonnegative_int(state.get("accepted_by_adjudication"))
        or not _nonnegative_int(state.get("accepted_by_final_review"))
        or not _nonnegative_int(state.get("accepted_by_human"))
        or not _nonnegative_int(state.get("needs_human"))
        or not _finite_number(state.get("actual_total_cost_usd"))
    ):
        raise SpeakerReviewSubmissionProcessingError("run state invalid")
    actual_total = (
        float(state["actual_primary_cost_usd"])
        + float(state["actual_adjudication_cost_usd"])
        + float(state["actual_final_review_cost_usd"])
    )
    if not math.isclose(
        float(state["actual_total_cost_usd"]), actual_total, rel_tol=0, abs_tol=1e-9
    ):
        raise SpeakerReviewSubmissionProcessingError("run state invalid")
    if (
        state["primary_completed_part_count"] != 0
        or state["adjudication_part_count"] != 0
        or state["adjudication_completed_part_count"] != 0
        or state["final_review_part_count"] != 0
        or state["final_review_completed_part_count"] != 0
        or state["primary_part_count"] <= 0
        or state["primary_batch_ids"]
        or state["primary_input_file_ids"]
        or state["adjudication_batch_ids"]
        or state["adjudication_input_file_ids"]
        or state["final_review_batch_ids"]
        or state["final_review_input_file_ids"]
    ):
        if state["status"] == "prepared":
            raise SpeakerReviewSubmissionProcessingError("run state invalid")
    if state["status"] == "prepared":
        if (
            state["primary_batch_id"] is not None
            or state["primary_input_file_id"] is not None
            or state["adjudication_batch_id"] is not None
            or state["adjudication_input_file_id"] is not None
            or state["final_review_batch_id"] is not None
            or state["final_review_input_file_id"] is not None
        ):
            raise SpeakerReviewSubmissionProcessingError("run state invalid")
    elif state["status"] == "primary_submitted":
        if (
            not _safe_text(state["primary_batch_id"])
            or not _safe_text(state["primary_input_file_id"])
            or len(state["primary_batch_ids"]) != 1
            or len(state["primary_input_file_ids"]) != 1
            or state["primary_batch_ids"][0] != state["primary_batch_id"]
            or state["primary_input_file_ids"][0] != state["primary_input_file_id"]
            or state["adjudication_batch_id"] is not None
            or state["adjudication_input_file_id"] is not None
            or state["final_review_batch_id"] is not None
            or state["final_review_input_file_id"] is not None
        ):
            raise SpeakerReviewSubmissionProcessingError("run state invalid")
    else:
        raise SpeakerReviewSubmissionProcessingError("run state invalid")
    return dict(state)


def _validate_preparation_receipt(request: Mapping[str, object]) -> PreparationBinding:
    digest = str(request["archive_sha256"])
    _validate_directory(PREPARATION_RECEIPTS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    path = PREPARATION_RECEIPTS_ROOT / f"sha256-{digest}.json"
    raw, _ = _read_stable_file(
        path,
        maximum=ROOT_RECORD_MAX_BYTES,
        mode=0o600,
        owner=(ROOT_UID, ROOT_GID),
        canonical=True,
    )
    try:
        receipt = _decode_json(raw, maximum=ROOT_RECORD_MAX_BYTES, canonical=True)
    except ValueError as error:
        raise SpeakerReviewSubmissionProcessingError("preparation receipt invalid") from error
    if set(receipt) != _PREPARATION_RECEIPT_KEYS:
        raise SpeakerReviewSubmissionProcessingError("preparation receipt invalid")
    if (
        receipt.get("schema_version") != 1
        or receipt.get("archive_sha256") != digest
        or type(receipt.get("artifact_file_count")) is not int
        or receipt["artifact_file_count"] <= 0
        or not _is_sha256(receipt.get("artifact_set_sha256"))
        or not _is_sha256(receipt.get("catalogue_sha256"))
        or not _is_sha256(receipt.get("configuration_sha256"))
        or not _safe_text(receipt.get("image_reference"), maximum=512)
        or not _is_release_sha(receipt.get("release_sha"))
    ):
        raise SpeakerReviewSubmissionProcessingError("preparation receipt invalid")
    active_release_sha, active_image_reference, active_configuration_sha256 = (
        _active_runtime_binding()
    )
    if (
        receipt["release_sha"] != active_release_sha
        or receipt["image_reference"] != active_image_reference
        or receipt["configuration_sha256"] != active_configuration_sha256
    ):
        raise SpeakerReviewSubmissionProcessingError("preparation runtime binding changed")
    result = receipt.get("result")
    if not isinstance(result, dict) or set(result) != _PREPARATION_RESULT_KEYS:
        raise SpeakerReviewSubmissionProcessingError("preparation receipt invalid")
    if (
        result.get("operation") != "prepare"
        or result.get("purpose") != contract.PURPOSE
        or result.get("season_number") != contract.SEASON_NUMBER
        or result.get("status") != "prepared"
        or type(result.get("file_count")) is not int
        or result["file_count"] <= 0
        or type(result.get("total_bytes")) is not int
        or result["total_bytes"] <= 0
        or type(result.get("candidate_count")) is not int
        or result["candidate_count"] <= 0
        or type(result.get("primary_part_count")) is not int
        or result["primary_part_count"] <= 0
        or not isinstance(result.get("run_id"), str)
    ):
        raise SpeakerReviewSubmissionProcessingError("preparation receipt invalid")
    try:
        checked_result = dict(result)
        cost_micros = _cost_microusd(result.get("estimated_primary_cost_usd"))
        contract.validate_aggregate(
            {
                "estimated_primary_cost_microusd": cost_micros,
                "operation": contract.OPERATION,
                "primary_part_count": result["primary_part_count"],
                "purpose": contract.PURPOSE,
                "run_id": result["run_id"],
                "season_number": contract.SEASON_NUMBER,
                "status": "submitted",
                "submitted_part_count": 1,
            }
        )
        checked_result["estimated_primary_cost_usd"] = result["estimated_primary_cost_usd"]
    except (TypeError, ValueError, SpeakerReviewSubmissionProcessingError) as error:
        raise SpeakerReviewSubmissionProcessingError("preparation receipt invalid") from error
    if checked_result["run_id"] != request["run_id"] or cost_micros > int(
        request["maximum_authorized_cost_microusd"]
    ):
        raise SpeakerReviewSubmissionProcessingError("preparation does not match request")
    return PreparationBinding(
        receipt=receipt,
        receipt_sha256=_sha256(raw),
        result=checked_result,
        artifact_hashes={},
        artifact_file_count=int(receipt["artifact_file_count"]),
        artifact_set_sha256=str(receipt["artifact_set_sha256"]),
    )


def _validate_authorization(request: Mapping[str, object]) -> str:
    _validate_directory(AUTHORIZATION_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    authorization_id = str(request["authorization_id"])
    path = AUTHORIZATION_ROOT / f"{authorization_id}.json"
    raw, _ = _read_stable_file(
        path,
        maximum=contract.REQUEST_MAX_BYTES,
        mode=0o600,
        owner=(ROOT_UID, ROOT_GID),
        canonical=True,
    )
    try:
        authorization = contract.parse_request(raw)
    except (TypeError, ValueError) as error:
        raise SpeakerReviewSubmissionProcessingError("authorization invalid") from error
    if authorization != dict(request):
        raise SpeakerReviewSubmissionProcessingError("authorization does not match request")
    return _sha256(raw)


def _run_directory(archive_sha256: str, run_id: str) -> Path:
    _validate_directory(REVIEW_RUNS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    if not _is_sha256(archive_sha256):
        raise SpeakerReviewSubmissionProcessingError("prepared run invalid")
    container_root = REVIEW_RUNS_ROOT / f"{_OBJECT_DIRECTORY_PREFIX}{archive_sha256}"
    review_runs = container_root / _RUN_DIRECTORY_NAME
    _validate_directory(container_root, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    _validate_directory(review_runs, mode=0o700, owner=(WORKER_UID, WORKER_GID))
    path = review_runs / run_id
    try:
        resolved_parent = path.resolve(strict=False).parent
    except OSError as error:
        raise SpeakerReviewSubmissionProcessingError("prepared run invalid") from error
    if resolved_parent != review_runs.resolve(strict=True):
        raise SpeakerReviewSubmissionProcessingError("prepared run invalid")
    return path


def _read_run_snapshot(
    preparation: PreparationBinding,
    *,
    expected_artifact_hashes: Mapping[str, str] | None,
) -> RunSnapshot:
    run_directory = _run_directory(
        str(preparation.receipt["archive_sha256"]),
        str(preparation.result["run_id"]),
    )
    _validate_directory(run_directory, mode=0o700, owner=(WORKER_UID, WORKER_GID))
    base_names = _expected_base_names(int(preparation.result["primary_part_count"]))
    try:
        entries = tuple(run_directory.iterdir())
    except OSError as error:
        raise SpeakerReviewSubmissionProcessingError("prepared run invalid") from error
    names = {entry.name for entry in entries}
    allowed = base_names | {_WORKFLOW_INTENT_FILENAME, _WORKFLOW_COMPLETED_FILENAME}
    if not base_names <= names or not names <= allowed:
        raise SpeakerReviewSubmissionProcessingError("prepared run inventory invalid")
    base_contents: dict[str, bytes] = {}
    for name in sorted(base_names):
        content, _ = _read_stable_file(
            run_directory / name,
            maximum=RUN_STATE_MAX_BYTES if name == _RUN_STATE_FILENAME else RUN_ARTIFACT_MAX_BYTES,
            mode=0o600,
            owner=(WORKER_UID, WORKER_GID),
        )
        base_contents[name] = content
    try:
        state_payload = _decode_json(
            base_contents[_RUN_STATE_FILENAME],
            maximum=RUN_STATE_MAX_BYTES,
            canonical=False,
        )
    except ValueError as error:
        raise SpeakerReviewSubmissionProcessingError("run state invalid") from error
    state = _validate_run_state(state_payload, preparation=preparation)
    base_hashes = {name: _sha256(content) for name, content in base_contents.items()}
    base_set_sha256 = _set_digest(base_contents)
    if expected_artifact_hashes is None:
        if state["status"] != "prepared":
            raise SpeakerReviewSubmissionProcessingError("prepared run state invalid")
        if (
            base_set_sha256 != preparation.artifact_set_sha256
            or len(base_contents) != preparation.artifact_file_count
        ):
            raise SpeakerReviewSubmissionProcessingError("prepared artifacts changed")
    else:
        expected = dict(expected_artifact_hashes)
        if set(expected) != base_names or any(not _is_sha256(value) for value in expected.values()):
            raise SpeakerReviewSubmissionProcessingError("submission intent invalid")
        for name, digest in base_hashes.items():
            if name != _RUN_STATE_FILENAME and digest != expected[name]:
                raise SpeakerReviewSubmissionProcessingError("prepared artifacts changed")
        if (
            state["status"] == "prepared"
            and base_hashes[_RUN_STATE_FILENAME] != expected[_RUN_STATE_FILENAME]
        ):
            raise SpeakerReviewSubmissionProcessingError("prepared run state changed")
    journal_names = tuple(sorted(names - base_names))
    journal_contents: dict[str, bytes] = {}
    for name in journal_names:
        content, _ = _read_stable_file(
            run_directory / name,
            maximum=ROOT_RECORD_MAX_BYTES,
            mode=0o600,
            owner=(WORKER_UID, WORKER_GID),
            canonical=True,
        )
        journal_contents[name] = content
    journal_phase = _validate_workflow_journals(
        run_directory,
        base_contents,
        journal_contents,
        state,
    )
    all_contents = {**base_contents, **journal_contents}
    return RunSnapshot(
        run_directory=run_directory,
        state=state,
        base_contents=base_contents,
        all_contents=all_contents,
        base_hashes=base_hashes,
        base_set_sha256=base_set_sha256,
        all_set_sha256=_set_digest(all_contents),
        journal_set_sha256=_set_digest(journal_contents),
        journal_names=journal_names,
        journal_phase=journal_phase,
    )


def _validate_workflow_binding(
    binding: object,
    *,
    request_sha256: str,
    run_id: str,
    prompt_version: str,
) -> dict[str, object]:
    if (
        not isinstance(binding, dict)
        or set(binding) != _WORKFLOW_BINDING_KEYS
        or binding.get("schema_version") != 1
        or binding.get("request_sha256") != request_sha256
        or binding.get("run_id") != run_id
        or binding.get("stage") != "primary"
        or binding.get("part") != 1
        or binding.get("prompt_version") != prompt_version
        or not _safe_text(binding.get("batch_endpoint"), maximum=128)
        or not _safe_text(binding.get("completion_window"), maximum=64)
    ):
        raise SpeakerReviewSubmissionProcessingError("submission journal invalid")
    return dict(binding)


def _validate_workflow_journals(
    run_directory: Path,
    base_contents: Mapping[str, bytes],
    journal_contents: Mapping[str, bytes],
    state: Mapping[str, object],
) -> str:
    del run_directory
    allowed = {_WORKFLOW_INTENT_FILENAME, _WORKFLOW_COMPLETED_FILENAME}
    if not set(journal_contents) <= allowed:
        raise SpeakerReviewSubmissionProcessingError("submission journal inventory invalid")
    request_name = _PRIMARY_REQUEST_FILENAME_TEMPLATE.format(part_number=1)
    request_sha256 = _sha256(base_contents[request_name])
    prompt_version = str(state["prompt_version"])
    intent: dict[str, object] | None = None
    completed: dict[str, object] | None = None
    if _WORKFLOW_INTENT_FILENAME in journal_contents:
        try:
            intent_value = _decode_json(
                journal_contents[_WORKFLOW_INTENT_FILENAME],
                maximum=ROOT_RECORD_MAX_BYTES,
                canonical=True,
            )
        except ValueError as error:
            raise SpeakerReviewSubmissionProcessingError("submission journal invalid") from error
        if set(intent_value) != {"binding", "status"} or intent_value.get("status") != "intent":
            raise SpeakerReviewSubmissionProcessingError("submission journal invalid")
        intent = {
            **intent_value,
            "binding": _validate_workflow_binding(
                intent_value.get("binding"),
                request_sha256=request_sha256,
                run_id=str(state["run_id"]),
                prompt_version=prompt_version,
            ),
        }
    if _WORKFLOW_COMPLETED_FILENAME in journal_contents:
        try:
            completed_value = _decode_json(
                journal_contents[_WORKFLOW_COMPLETED_FILENAME],
                maximum=ROOT_RECORD_MAX_BYTES,
                canonical=True,
            )
        except ValueError as error:
            raise SpeakerReviewSubmissionProcessingError("submission journal invalid") from error
        if set(completed_value) != {"binding", "batch_id", "input_file_id", "status"}:
            raise SpeakerReviewSubmissionProcessingError("submission journal invalid")
        if not all(
            _safe_text(completed_value.get(key), maximum=512)
            for key in ("batch_id", "input_file_id", "status")
        ):
            raise SpeakerReviewSubmissionProcessingError("submission journal invalid")
        completed = {
            **completed_value,
            "binding": _validate_workflow_binding(
                completed_value.get("binding"),
                request_sha256=request_sha256,
                run_id=str(state["run_id"]),
                prompt_version=prompt_version,
            ),
        }
    if completed is not None and intent is None:
        return "orphan_completed"
    if intent is not None and completed is None:
        return "intent"
    if intent is not None and completed is not None:
        if intent["binding"] != completed["binding"]:
            raise SpeakerReviewSubmissionProcessingError("submission journal binding changed")
        if state["status"] == "primary_submitted" and (
            state["primary_batch_id"] != completed["batch_id"]
            or state["primary_input_file_id"] != completed["input_file_id"]
        ):
            raise SpeakerReviewSubmissionProcessingError("submission state binding changed")
        return "completed"
    return "none"


def _root_intent_path(run_id: str) -> Path:
    return SUBMISSION_RECEIPTS_ROOT / f"{run_id}.intent.json"


def _root_receipt_path(run_id: str) -> Path:
    return SUBMISSION_RECEIPTS_ROOT / f"{run_id}.json"


def _root_binding(
    request: Mapping[str, object],
    *,
    authorization_sha256: str,
    preparation: PreparationBinding,
    artifact_hashes: Mapping[str, str],
) -> dict[str, object]:
    return {
        "archive_sha256": request["archive_sha256"],
        "authorization_id": request["authorization_id"],
        "authorization_sha256": authorization_sha256,
        "estimated_primary_cost_microusd": _cost_microusd(
            preparation.result["estimated_primary_cost_usd"]
        ),
        "maximum_authorized_cost_microusd": request["maximum_authorized_cost_microusd"],
        "operation": contract.OPERATION,
        "prep_receipt_sha256": preparation.receipt_sha256,
        "prepared_artifact_file_count": preparation.artifact_file_count,
        "prepared_artifact_set_sha256": preparation.artifact_set_sha256,
        "prepared_artifact_hashes": dict(sorted(artifact_hashes.items())),
        "primary_part_count": preparation.result["primary_part_count"],
        "purpose": contract.PURPOSE,
        "run_id": preparation.result["run_id"],
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "season_number": contract.SEASON_NUMBER,
        "status": "intent",
    }


def _validate_root_intent(
    value: object,
    *,
    expected: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _ROOT_INTENT_KEYS:
        raise SpeakerReviewSubmissionProcessingError("submission intent invalid")
    if value != dict(expected):
        raise SpeakerReviewSubmissionProcessingError("submission authorization changed")
    hashes = value.get("prepared_artifact_hashes")
    if not isinstance(hashes, dict) or not all(_is_sha256(item) for item in hashes.values()):
        raise SpeakerReviewSubmissionProcessingError("submission intent invalid")
    return dict(value)


def _read_root_record(path: Path, *, maximum: int) -> dict[str, object]:
    raw, _ = _read_stable_file(
        path,
        maximum=maximum,
        mode=0o600,
        owner=(ROOT_UID, ROOT_GID),
        canonical=True,
    )
    try:
        return _decode_json(raw, maximum=maximum, canonical=True)
    except ValueError as error:
        raise SpeakerReviewSubmissionProcessingError("submission receipt invalid") from error


def _create_root_record(path: Path, value: Mapping[str, object]) -> None:
    encoded = _canonical_json(value)
    if len(encoded) > ROOT_RECORD_MAX_BYTES:
        raise SpeakerReviewSubmissionProcessingError("submission receipt too large")
    _validate_directory(SUBMISSION_RECEIPTS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    descriptor = -1
    try:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "posix":
            directory = os.open(
                SUBMISSION_RECEIPTS_ROOT,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except FileExistsError as error:
        raise SpeakerReviewSubmissionProcessingError("submission receipt conflict") from error
    except OSError as error:
        raise SpeakerReviewSubmissionProcessingError("submission receipt unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _aggregate(
    preparation: PreparationBinding,
    *,
    status: str,
    submitted_part_count: int,
) -> dict[str, object]:
    result = {
        "estimated_primary_cost_microusd": _cost_microusd(
            preparation.result["estimated_primary_cost_usd"]
        ),
        "operation": contract.OPERATION,
        "primary_part_count": preparation.result["primary_part_count"],
        "purpose": contract.PURPOSE,
        "run_id": preparation.result["run_id"],
        "season_number": contract.SEASON_NUMBER,
        "status": status,
        "submitted_part_count": submitted_part_count,
    }
    try:
        return contract.validate_aggregate(result, status=status)
    except (TypeError, ValueError) as error:
        raise SpeakerReviewSubmissionProcessingError("submission aggregate invalid") from error


def _validate_worker_result(
    value: object,
    preparation: PreparationBinding,
) -> dict[str, object]:
    try:
        result = contract.validate_aggregate(value)
    except (TypeError, ValueError) as error:
        raise SpeakerReviewSubmissionProcessingError("submission worker result invalid") from error
    expected_cost = _cost_microusd(preparation.result["estimated_primary_cost_usd"])
    if (
        result["run_id"] != preparation.result["run_id"]
        or result["primary_part_count"] != preparation.result["primary_part_count"]
        or result["estimated_primary_cost_microusd"] != expected_cost
        or result["submitted_part_count"] not in (0, 1)
    ):
        raise SpeakerReviewSubmissionProcessingError("submission worker result invalid")
    if result["status"] == "reconciliation_required" and result["submitted_part_count"] != 0:
        raise SpeakerReviewSubmissionProcessingError("submission worker result invalid")
    if (
        result["status"] in {"submitted", "already_submitted"}
        and result["submitted_part_count"] != 1
    ):
        raise SpeakerReviewSubmissionProcessingError("submission worker result invalid")
    return result


def _safe_compose_environment() -> dict[str, str]:
    # Never inherit OPENAI_API_KEY or any caller-provided environment into the
    # Compose CLI.  Compose reads the root-controlled env file itself; the
    # request values are supplied as explicit, non-secret --env arguments.
    return {
        "PATH": "/usr/sbin:/usr/bin",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _worker_arguments(request: Mapping[str, object], review_runs: Path) -> list[str]:
    return [
        "docker",
        "compose",
        "--progress",
        "quiet",
        "--env-file",
        os.fspath(DEV_ENV_FILE),
        "--profile",
        COMPOSE_PROFILE,
        "-f",
        os.fspath(COMPOSE_PATH),
        "run",
        "--name",
        CONTAINER_NAME,
        "--rm",
        "--no-TTY",
        "--no-deps",
        "--pull",
        "never",
        "--user",
        f"{WORKER_UID}:{WORKER_GID}",
        "--volume",
        f"{review_runs.as_posix()}:{WORKER_MOUNT}:rw",
        "--env",
        f"{contract.ENV_ARCHIVE_SHA256}={request['archive_sha256']}",
        "--env",
        f"{contract.ENV_RUN_ID}={request['run_id']}",
        "--env",
        f"{contract.ENV_AUTHORIZATION_ID}={request['authorization_id']}",
        "--env",
        f"{contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD}={request['maximum_authorized_cost_microusd']}",
        COMPOSE_SERVICE,
    ]


def _read_pipe(stream: BinaryIO) -> bytes:
    try:
        return stream.read(WORKER_OUTPUT_MAX_BYTES + 1)
    finally:
        stream.close()


def _terminate_worker(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix" and process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=WORKER_KILL_AFTER_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            process.wait()
    elif process.poll() is None:
        process.kill()
        process.wait()


def _cleanup_compose_worker() -> None:
    """Remove only a one-shot container, never a review-run evidence file."""

    cleanup = [
        [
            "docker",
            "compose",
            "--progress",
            "quiet",
            "--env-file",
            os.fspath(DEV_ENV_FILE),
            "--profile",
            COMPOSE_PROFILE,
            "-f",
            os.fspath(COMPOSE_PATH),
            "rm",
            "--force",
            "--stop",
            COMPOSE_SERVICE,
        ],
        ["docker", "rm", "--force", CONTAINER_NAME],
    ]
    for arguments in cleanup:
        try:
            subprocess.run(
                arguments,
                cwd=RELEASE_ROOT,
                env=_safe_compose_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                check=False,
                timeout=WORKER_KILL_AFTER_SECONDS,
            )
        except (OSError, subprocess.SubprocessError):
            continue


def _run_worker(request: Mapping[str, object], review_runs: Path) -> dict[str, object]:
    arguments = _worker_arguments(request, review_runs)
    process: subprocess.Popen[bytes] | None = None
    terminated = False
    # A host interruption can strand the fixed-name one-shot container. The
    # workflow journal is checked before this point, so removing a stale
    # container here cannot bypass an ambiguous provider intent.
    _cleanup_compose_worker()
    try:
        if os.name == "posix":
            process = subprocess.Popen(
                arguments,
                cwd=RELEASE_ROOT,
                env=_safe_compose_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=True,
            )
        else:
            process = subprocess.Popen(
                arguments,
                cwd=RELEASE_ROOT,
                env=_safe_compose_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
        if process.stdout is None or process.stderr is None:
            raise SpeakerReviewSubmissionProcessingError("submission worker failed")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            stdout_future = executor.submit(_read_pipe, process.stdout)
            stderr_future = executor.submit(_read_pipe, process.stderr)
            try:
                returncode = process.wait(timeout=WORKER_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as error:
                _terminate_worker(process)
                terminated = True
                stdout_future.result(timeout=5)
                stderr_future.result(timeout=5)
                raise SpeakerReviewSubmissionProcessingError("submission worker failed") from error
            stdout = stdout_future.result(timeout=5)
            stderr = stderr_future.result(timeout=5)
        if returncode != 0 or stderr or len(stdout) > WORKER_OUTPUT_MAX_BYTES:
            raise SpeakerReviewSubmissionProcessingError("submission worker failed")
        try:
            value = contract.parse_aggregate(stdout)
        except (TypeError, ValueError) as error:
            raise SpeakerReviewSubmissionProcessingError("submission worker failed") from error
        return value
    except SpeakerReviewSubmissionProcessingError:
        raise
    except (OSError, subprocess.SubprocessError, TimeoutError) as error:
        raise SpeakerReviewSubmissionProcessingError("submission worker failed") from error
    finally:
        if process is not None and not terminated and process.poll() is None:
            try:
                _terminate_worker(process)
            except (OSError, subprocess.SubprocessError):
                pass
        _cleanup_compose_worker()


def _intent_payload(
    request: Mapping[str, object],
    *,
    authorization_sha256: str,
    preparation: PreparationBinding,
    artifact_hashes: Mapping[str, str],
) -> dict[str, object]:
    return _root_binding(
        request,
        authorization_sha256=authorization_sha256,
        preparation=preparation,
        artifact_hashes=artifact_hashes,
    )


def _receipt_payload(
    intent: Mapping[str, object],
    *,
    result: Mapping[str, object],
    snapshot: RunSnapshot,
) -> dict[str, object]:
    return {
        **dict(intent),
        "status": result["status"],
        "post_artifact_file_count": len(snapshot.all_contents),
        "post_artifact_set_sha256": snapshot.all_set_sha256,
        "post_journal_file_count": len(snapshot.journal_names),
        "post_journal_set_sha256": snapshot.journal_set_sha256,
        "post_run_state_sha256": _sha256(snapshot.base_contents[_RUN_STATE_FILENAME]),
        "result": dict(result),
    }


def _validate_final_receipt(
    value: object,
    *,
    intent: Mapping[str, object],
    snapshot: RunSnapshot,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _ROOT_RECEIPT_KEYS:
        raise SpeakerReviewSubmissionProcessingError("submission receipt invalid")
    for key, expected in intent.items():
        if key == "status":
            continue
        if value.get(key) != expected:
            raise SpeakerReviewSubmissionProcessingError("submission receipt binding changed")
    if value.get("status") not in {"submitted", "already_submitted"}:
        raise SpeakerReviewSubmissionProcessingError("submission receipt invalid")
    if (
        type(value.get("post_artifact_file_count")) is not int
        or value["post_artifact_file_count"] != len(snapshot.all_contents)
        or not _is_sha256(value.get("post_artifact_set_sha256"))
        or value["post_artifact_set_sha256"] != snapshot.all_set_sha256
        or type(value.get("post_journal_file_count")) is not int
        or value["post_journal_file_count"] != len(snapshot.journal_names)
        or not _is_sha256(value.get("post_journal_set_sha256"))
        or value["post_journal_set_sha256"] != snapshot.journal_set_sha256
        or not _is_sha256(value.get("post_run_state_sha256"))
        or value["post_run_state_sha256"] != _sha256(snapshot.base_contents[_RUN_STATE_FILENAME])
        or snapshot.state["status"] != "primary_submitted"
        or snapshot.journal_phase != "completed"
    ):
        raise SpeakerReviewSubmissionProcessingError("submission receipt invalid")
    try:
        result = contract.validate_aggregate(value.get("result"))
    except (TypeError, ValueError) as error:
        raise SpeakerReviewSubmissionProcessingError("submission receipt invalid") from error
    if result["status"] not in {"submitted", "already_submitted"}:
        raise SpeakerReviewSubmissionProcessingError("submission receipt invalid")
    return result


def _post_submit_snapshot(
    preparation: PreparationBinding,
    *,
    artifact_hashes: Mapping[str, str],
) -> RunSnapshot:
    snapshot = _read_run_snapshot(
        preparation,
        expected_artifact_hashes=artifact_hashes,
    )
    if snapshot.state["status"] != "primary_submitted" or snapshot.journal_phase != "completed":
        raise SpeakerReviewSubmissionProcessingError("post-submit evidence invalid")
    if set(snapshot.journal_names) != {
        _WORKFLOW_INTENT_FILENAME,
        _WORKFLOW_COMPLETED_FILENAME,
    }:
        raise SpeakerReviewSubmissionProcessingError("post-submit inventory invalid")
    return snapshot


def process_request(request: Mapping[str, object]) -> dict[str, object]:
    """Process one canonical request without exposing private/provider data."""

    try:
        validated_request = contract.validate_request(request)
    except (TypeError, ValueError) as error:
        raise SpeakerReviewSubmissionProcessingError("invalid submission request") from error
    authorization_sha256 = _validate_authorization(validated_request)
    preparation = _validate_preparation_receipt(validated_request)
    _validate_directory(SUBMISSION_RECEIPTS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    intent_path = _root_intent_path(str(validated_request["run_id"]))
    receipt_path = _root_receipt_path(str(validated_request["run_id"]))

    receipt_exists = os.path.lexists(receipt_path)
    intent_exists = os.path.lexists(intent_path)
    if receipt_exists:
        if not intent_exists:
            raise SpeakerReviewSubmissionProcessingError("orphan submission receipt")
        intent_value = _read_root_record(intent_path, maximum=ROOT_RECORD_MAX_BYTES)
        # The expected artifact hashes are in the immutable root intent.  A
        # matching final receipt is accepted only after all post-submit files,
        # journals, state, and current authorization/preparation bindings are
        # revalidated.
        artifact_hashes = intent_value.get("prepared_artifact_hashes")
        if not isinstance(artifact_hashes, dict):
            raise SpeakerReviewSubmissionProcessingError("submission intent invalid")
        expected_intent = _intent_payload(
            validated_request,
            authorization_sha256=authorization_sha256,
            preparation=preparation,
            artifact_hashes=artifact_hashes,
        )
        intent = _validate_root_intent(intent_value, expected=expected_intent)
        snapshot = _read_run_snapshot(
            preparation,
            expected_artifact_hashes=intent["prepared_artifact_hashes"],
        )
        receipt = _read_root_record(receipt_path, maximum=ROOT_RECORD_MAX_BYTES)
        _validate_final_receipt(receipt, intent=intent, snapshot=snapshot)
        return _aggregate(preparation, status="already_submitted", submitted_part_count=1)

    if intent_exists:
        intent_value = _read_root_record(intent_path, maximum=ROOT_RECORD_MAX_BYTES)
        # The expected hashes are part of the immutable root intent.  This
        # remains verifiable after the worker has changed run-state.json.
        artifact_hashes = intent_value.get("prepared_artifact_hashes")
        if not isinstance(artifact_hashes, dict):
            raise SpeakerReviewSubmissionProcessingError("submission intent invalid")
        expected_intent = _intent_payload(
            validated_request,
            authorization_sha256=authorization_sha256,
            preparation=preparation,
            artifact_hashes=artifact_hashes,
        )
        intent = _validate_root_intent(intent_value, expected=expected_intent)
    else:
        initial = _read_run_snapshot(preparation, expected_artifact_hashes=None)
        if initial.state["status"] != "prepared":
            raise SpeakerReviewSubmissionProcessingError("missing immutable submission intent")
        intent = _intent_payload(
            validated_request,
            authorization_sha256=authorization_sha256,
            preparation=preparation,
            artifact_hashes=initial.base_hashes,
        )
        _create_root_record(intent_path, intent)

    artifact_hashes = intent["prepared_artifact_hashes"]
    snapshot = _read_run_snapshot(
        preparation,
        expected_artifact_hashes=artifact_hashes,
    )
    if snapshot.state["status"] == "primary_submitted" and snapshot.journal_phase != "completed":
        raise SpeakerReviewSubmissionProcessingError("post-submit journals missing")
    if snapshot.journal_phase in {"intent", "orphan_completed"}:
        # An intent without a completed journal is deliberately never retried:
        # the provider may have accepted the request before the client crashed.
        return _aggregate(preparation, status="reconciliation_required", submitted_part_count=0)

    worker_result = _run_worker(validated_request, snapshot.run_directory.parent)
    worker_result = _validate_worker_result(worker_result, preparation)
    if worker_result["status"] == "reconciliation_required":
        unresolved = _read_run_snapshot(
            preparation,
            expected_artifact_hashes=artifact_hashes,
        )
        if unresolved.journal_phase not in {"intent", "orphan_completed"}:
            raise SpeakerReviewSubmissionProcessingError("reconciliation state invalid")
        return _aggregate(preparation, status="reconciliation_required", submitted_part_count=0)

    post = _post_submit_snapshot(preparation, artifact_hashes=artifact_hashes)
    effective_status = (
        "already_submitted"
        if snapshot.journal_phase == "completed" or worker_result["status"] == "already_submitted"
        else "submitted"
    )
    result = _aggregate(preparation, status=effective_status, submitted_part_count=1)
    receipt = _receipt_payload(intent, result=result, snapshot=post)
    _create_root_record(receipt_path, receipt)
    return result


def _require_root_context() -> None:
    if (
        os.name != "posix"
        or not hasattr(os, "geteuid")
        or os.geteuid() != ROOT_UID
        or os.environ.get("SUDO_USER") != "cinegraph-review"
        or platform.system() != "Linux"
        or platform.machine() != "x86_64"
    ):
        raise SpeakerReviewSubmissionProcessingError("invalid submission caller")


def main() -> int:
    try:
        _require_root_context()
        request = _read_request(sys.stdin.buffer)
        result = process_request(request)
        payload = contract.canonical_json(result)
        if len(payload) > contract.OUTPUT_MAX_BYTES:
            raise SpeakerReviewSubmissionProcessingError("submission aggregate too large")
        sys.stdout.buffer.write(payload)
        return 0
    except Exception:
        # Never return exception text, paths, provider identifiers, or secret
        # names over the forced-command boundary.
        sys.stderr.buffer.write(
            contract.canonical_json(
                {"error": "speaker_review_submission_rejected", "status": "error"}
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
