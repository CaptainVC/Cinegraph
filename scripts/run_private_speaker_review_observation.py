"""Root-only coordinator for one paid primary Batch observation.

The coordinator is deliberately separate from submission.  It creates an
observation intent once, invokes the read-only Phase 62 worker when needed,
and writes a final receipt only after the run-state and evidence transition
has been verified.  This module is standard-library-only and is safe to run
with ``python3 -I -S -B``.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import os
import platform
import signal
import stat
import subprocess
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import BinaryIO, Final, Mapping

_SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if os.fspath(_SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, os.fspath(_SCRIPT_DIRECTORY))

try:
    from scripts import private_speaker_review_observation_contract as contract
    from scripts import run_private_speaker_review_submission as submission
except ModuleNotFoundError:
    import private_speaker_review_observation_contract as contract
    import run_private_speaker_review_submission as submission


class SpeakerReviewObservationProcessingError(RuntimeError):
    """A path-free, provider-free root-coordinator rejection."""


RELEASE_ROOT: Final = Path(__file__).resolve().parents[1]
SPEAKER_REVIEW_ROOT: Final = Path("/opt/cinegraph/shared/private-corpus/dev/speaker-review")
PREPARATION_RECEIPTS_ROOT: Final = SPEAKER_REVIEW_ROOT / "receipts"
REVIEW_RUNS_ROOT: Final = SPEAKER_REVIEW_ROOT.parent / "review-runs"
AUTHORIZATION_ROOT: Final = SPEAKER_REVIEW_ROOT / "authorization"
SUBMISSION_RECEIPTS_ROOT: Final = SPEAKER_REVIEW_ROOT / "submission-receipts"
OBSERVATION_RECEIPTS_ROOT: Final = SPEAKER_REVIEW_ROOT / "observation-receipts"
DEV_ENV_FILE: Final = Path("/etc/cinegraph/dev.env")

COMPOSE_PROFILE: Final = "corpus-speaker-review-observe-primary"
COMPOSE_SERVICE: Final = "corpus-speaker-review-observe-primary"
CONTAINER_NAME: Final = "cinegraph-speaker-review-observe-primary"
COMPOSE_PATH: Final = RELEASE_ROOT / "deploy/compose.yaml"
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

_RUN_STATE_FILENAME: Final = "run-state.json"
_CANDIDATES_FILENAME: Final = "candidates.jsonl"
_SOURCE_MANIFEST_FILENAME: Final = "source-manifest.json"
_PRIMARY_REQUEST_FILENAME_TEMPLATE: Final = "primary-part-{part_number:04d}-requests.jsonl"
_WORKFLOW_INTENT_FILENAME: Final = ".primary-part-0001-submission-intent.json"
_WORKFLOW_COMPLETED_FILENAME: Final = ".primary-part-0001-submission-completed.json"
_OBSERVATION_OUTPUT_NAMES: Final = frozenset(
    {
        "primary-part-0001-output.jsonl",
        "primary-part-0001-api-errors.jsonl",
        "terminal-api-errors.jsonl",
    }
)


def _part_output_names(part_number: int) -> frozenset[str]:
    prefix = f"primary-part-{part_number:04d}"
    return frozenset({f"{prefix}-output.jsonl", f"{prefix}-api-errors.jsonl"})


_RUN_STATUSES: Final = frozenset(
    {
        "primary_submitted",
        "primary_part_completed",
        "failed",
    }
)
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
_SUBMISSION_RECEIPT_KEYS: Final = frozenset(
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
        "post_artifact_file_count",
        "post_artifact_set_sha256",
        "post_journal_file_count",
        "post_journal_set_sha256",
        "post_run_state_sha256",
        "result",
    }
)
_INTENT_KEYS: Final = frozenset(
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
        "primary_part_number",
        "pre_observation_primary_completed_part_count",
        "pre_observation_observation_names",
        "pre_observation_evidence_file_count",
        "pre_observation_evidence_set_sha256",
        "pre_observation_state_binding_sha256",
        "pre_observation_run_state_sha256",
        "purpose",
        "run_id",
        "schema_version",
        "season_number",
        "status",
        "submission_receipt_sha256",
    }
)
_RECEIPT_KEYS: Final = frozenset(
    {
        *_INTENT_KEYS,
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
    artifact_file_count: int
    artifact_set_sha256: str


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    run_directory: Path
    state: dict[str, object]
    base_contents: dict[str, bytes]
    all_contents: dict[str, bytes]
    base_hashes: dict[str, str]
    all_set_sha256: str
    journal_set_sha256: str
    journal_names: tuple[str, ...]
    observation_names: tuple[str, ...]
    journal_phase: str


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _read_request(stream: BinaryIO) -> dict[str, object]:
    raw = stream.readline(contract.REQUEST_MAX_BYTES + 1)
    if stream.read(1):
        raise SpeakerReviewObservationProcessingError("invalid observation request")
    try:
        return contract.parse_request(raw)
    except (TypeError, ValueError) as error:
        raise SpeakerReviewObservationProcessingError("invalid observation request") from error


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _safe_text(value: object, maximum: int = 512) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and value == value.strip()
        and not any(ord(character) < 32 for character in value)
    )


def _finite_nonnegative(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0
    )


def _cost_microusd(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpeakerReviewObservationProcessingError("observation cost invalid")
    if not math.isfinite(float(value)) or float(value) < 0:
        raise SpeakerReviewObservationProcessingError("observation cost invalid")
    try:
        micros = Decimal(str(value)) * Decimal(1_000_000)
    except (InvalidOperation, ValueError) as error:
        raise SpeakerReviewObservationProcessingError("observation cost invalid") from error
    if micros != micros.to_integral_value():
        raise SpeakerReviewObservationProcessingError("observation cost invalid")
    result = int(micros)
    if result > contract.MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise SpeakerReviewObservationProcessingError("observation cost invalid")
    return result


def _configuration_sha256(release: Path = RELEASE_ROOT) -> str:
    return submission._configuration_sha256(release)


def _release_image_reference() -> str:
    return submission._release_image_reference()


def _active_runtime_binding() -> tuple[str, str, str]:
    return submission._active_runtime_binding()


def _validate_directory(path: Path, *, mode: int, owner: tuple[int, int]) -> None:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise SpeakerReviewObservationProcessingError("observation evidence unavailable") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or (os.name == "posix" and stat.S_IMODE(metadata.st_mode) != mode)
        or (os.name == "posix" and (metadata.st_uid, metadata.st_gid) != owner)
        or resolved != path
    ):
        raise SpeakerReviewObservationProcessingError("observation evidence unavailable")


def _read_stable_file(path: Path, *, maximum: int, mode: int, owner: tuple[int, int]) -> bytes:
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
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            content = os.read(descriptor, maximum + 1)
        finally:
            os.close(descriptor)
        after = path.lstat()

        def identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
            return (
                value.st_dev,
                value.st_ino,
                value.st_size,
                value.st_mtime_ns,
                value.st_nlink,
            )

        if identity(before) != identity(opened) or identity(opened) != identity(after):
            raise OSError
        if len(content) != opened.st_size or len(content) > maximum:
            raise OSError
        return content
    except OSError as error:
        raise SpeakerReviewObservationProcessingError("observation evidence unavailable") from error


def _decode_json(raw: bytes, *, canonical: bool = False) -> dict[str, object]:
    def reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = item
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise SpeakerReviewObservationProcessingError("observation evidence invalid") from error
    if not isinstance(value, dict) or (canonical and _canonical_json(value) != raw):
        raise SpeakerReviewObservationProcessingError("observation evidence invalid")
    return value


def _expected_base_names(part_count: int) -> set[str]:
    if type(part_count) is not int or part_count <= 0 or part_count > 1024:
        raise SpeakerReviewObservationProcessingError("observation run invalid")
    return {
        _CANDIDATES_FILENAME,
        _SOURCE_MANIFEST_FILENAME,
        _RUN_STATE_FILENAME,
        *(
            _PRIMARY_REQUEST_FILENAME_TEMPLATE.format(part_number=part)
            for part in range(1, part_count + 1)
        ),
    }


def _set_digest(contents: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(contents):
        encoded = name.encode("ascii")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(contents[name]).to_bytes(8, "big"))
        digest.update(contents[name])
    return digest.hexdigest()


def _state_binding_sha256(state: Mapping[str, object]) -> str:
    fixed = dict(state)
    for key in (
        "status",
        "updated_at",
        "primary_completed_part_count",
    ):
        fixed.pop(key, None)
    return _sha256(_canonical_json(fixed))


def _validate_run_state(
    value: object,
    *,
    preparation: PreparationBinding,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _RUN_STATE_KEYS:
        raise SpeakerReviewObservationProcessingError("observation run state invalid")
    if (
        value.get("schema_version") != 5
        or value.get("run_id") != preparation.result["run_id"]
        or value.get("status") not in _RUN_STATUSES
        or value.get("candidate_count") != preparation.result["candidate_count"]
        or value.get("primary_part_count") != preparation.result["primary_part_count"]
        or not _safe_text(value.get("created_at"), 128)
        or not _safe_text(value.get("updated_at"), 128)
        or not _safe_text(value.get("primary_model"), 128)
        or not _safe_text(value.get("adjudication_model"), 128)
        or not _safe_text(value.get("prompt_version"), 128)
        or not isinstance(value.get("final_review_model"), str)
        or len(value["final_review_model"]) > 128
        or any(ord(character) < 32 for character in value["final_review_model"])
        or any(
            not _finite_nonnegative(value.get(field))
            for field in (
                "maximum_cost_usd",
                "actual_primary_cost_usd",
                "actual_adjudication_cost_usd",
                "actual_final_review_cost_usd",
                "actual_total_cost_usd",
            )
        )
        or _cost_microusd(value.get("estimated_primary_cost_usd"))
        != _cost_microusd(preparation.result["estimated_primary_cost_usd"])
    ):
        raise SpeakerReviewObservationProcessingError("observation run state invalid")
    for field in (
        "primary_completed_part_count",
        "adjudication_part_count",
        "adjudication_completed_part_count",
        "final_review_part_count",
        "final_review_completed_part_count",
        "final_review_retry_count",
        "accepted_by_consensus",
        "accepted_by_adjudication",
        "accepted_by_final_review",
        "accepted_by_human",
        "needs_human",
    ):
        if type(value.get(field)) is not int or value[field] < 0:
            raise SpeakerReviewObservationProcessingError("observation run state invalid")
    if value["primary_completed_part_count"] > value["primary_part_count"]:
        raise SpeakerReviewObservationProcessingError("observation run state invalid")
    if not math.isclose(
        float(value["actual_total_cost_usd"]),
        float(value["actual_primary_cost_usd"])
        + float(value["actual_adjudication_cost_usd"])
        + float(value["actual_final_review_cost_usd"]),
        rel_tol=0,
        abs_tol=1e-9,
    ):
        raise SpeakerReviewObservationProcessingError("observation run state invalid")
    for field in (
        "primary_batch_ids",
        "primary_input_file_ids",
        "adjudication_batch_ids",
        "adjudication_input_file_ids",
        "final_review_batch_ids",
        "final_review_input_file_ids",
    ):
        if not isinstance(value.get(field), list) or not all(
            _safe_text(item) for item in value[field]
        ):
            raise SpeakerReviewObservationProcessingError("observation run state invalid")
    for field in (
        "primary_batch_id",
        "primary_input_file_id",
        "adjudication_batch_id",
        "adjudication_input_file_id",
        "final_review_batch_id",
        "final_review_input_file_id",
    ):
        if value[field] is not None and not _safe_text(value[field]):
            raise SpeakerReviewObservationProcessingError("observation run state invalid")
    if value["status"] == "primary_submitted":
        if (
            value["primary_completed_part_count"] != 0
            or not _safe_text(value["primary_batch_id"])
            or not _safe_text(value["primary_input_file_id"])
            or value["primary_batch_ids"] != [value["primary_batch_id"]]
            or value["primary_input_file_ids"] != [value["primary_input_file_id"]]
        ):
            raise SpeakerReviewObservationProcessingError("observation run state invalid")
    elif value["status"] == "primary_part_completed":
        if (
            value["primary_completed_part_count"] != 1
            or not _safe_text(value["primary_batch_id"])
            or not _safe_text(value["primary_input_file_id"])
            or value["primary_batch_ids"] != [value["primary_batch_id"]]
            or value["primary_input_file_ids"] != [value["primary_input_file_id"]]
        ):
            raise SpeakerReviewObservationProcessingError("observation run state invalid")
    elif (
        value["primary_completed_part_count"] != 0
        or not _safe_text(value["primary_batch_id"])
        or not _safe_text(value["primary_input_file_id"])
        or value["primary_batch_ids"] != [value["primary_batch_id"]]
        or value["primary_input_file_ids"] != [value["primary_input_file_id"]]
    ):
        raise SpeakerReviewObservationProcessingError("observation run state invalid")
    return dict(value)


def _validate_preparation_receipt(request: Mapping[str, object]) -> PreparationBinding:
    digest = str(request["archive_sha256"])
    _validate_directory(PREPARATION_RECEIPTS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    path = PREPARATION_RECEIPTS_ROOT / f"sha256-{digest}.json"
    raw = _read_stable_file(
        path, maximum=ROOT_RECORD_MAX_BYTES, mode=0o600, owner=(ROOT_UID, ROOT_GID)
    )
    receipt = _decode_json(raw, canonical=True)
    if set(receipt) != _PREPARATION_RECEIPT_KEYS:
        raise SpeakerReviewObservationProcessingError("preparation receipt invalid")
    active = _active_runtime_binding()
    if (
        receipt.get("schema_version") != 1
        or receipt.get("archive_sha256") != digest
        or not _is_sha256(receipt.get("artifact_set_sha256"))
        or not _is_sha256(receipt.get("catalogue_sha256"))
        or receipt.get("release_sha") != active[0]
        or receipt.get("image_reference") != active[1]
        or receipt.get("configuration_sha256") != active[2]
        or type(receipt.get("artifact_file_count")) is not int
        or receipt["artifact_file_count"] <= 0
    ):
        raise SpeakerReviewObservationProcessingError("preparation receipt invalid")
    result = receipt.get("result")
    if not isinstance(result, dict) or set(result) != _PREPARATION_RESULT_KEYS:
        raise SpeakerReviewObservationProcessingError("preparation receipt invalid")
    if (
        result.get("operation") != "prepare"
        or result.get("purpose") != contract.PURPOSE
        or result.get("season_number") != contract.SEASON_NUMBER
        or result.get("status") != "prepared"
        or type(result.get("candidate_count")) is not int
        or result["candidate_count"] <= 0
        or type(result.get("primary_part_count")) is not int
        or result["primary_part_count"] <= 0
        or type(result.get("file_count")) is not int
        or result["file_count"] != receipt["artifact_file_count"]
        or type(result.get("total_bytes")) is not int
        or result["total_bytes"] <= 0
        or result["total_bytes"] > RUN_ARTIFACT_TOTAL_MAX_BYTES
        or result.get("run_id") != request["run_id"]
    ):
        raise SpeakerReviewObservationProcessingError("preparation receipt invalid")
    _cost_microusd(result.get("estimated_primary_cost_usd"))
    return PreparationBinding(
        receipt=receipt,
        receipt_sha256=_sha256(raw),
        result=dict(result),
        artifact_file_count=receipt["artifact_file_count"],
        artifact_set_sha256=str(receipt["artifact_set_sha256"]),
    )


def _validate_authorization(request: Mapping[str, object]) -> str:
    _validate_directory(AUTHORIZATION_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    path = AUTHORIZATION_ROOT / f"{request['authorization_id']}.json"
    raw = _read_stable_file(
        path, maximum=contract.REQUEST_MAX_BYTES, mode=0o600, owner=(ROOT_UID, ROOT_GID)
    )
    try:
        authorized = contract.parse_request(raw)
    except (TypeError, ValueError) as error:
        raise SpeakerReviewObservationProcessingError("authorization invalid") from error
    if authorized != dict(request):
        raise SpeakerReviewObservationProcessingError("authorization does not match request")
    return _sha256(raw)


def _run_directory(archive_sha256: str, run_id: str) -> Path:
    _validate_directory(REVIEW_RUNS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    if not _is_sha256(archive_sha256) or not isinstance(run_id, str):
        raise SpeakerReviewObservationProcessingError("observation run invalid")
    object_root = REVIEW_RUNS_ROOT / f"sha256-{archive_sha256}"
    review_runs = object_root / "review-runs"
    _validate_directory(object_root, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    _validate_directory(review_runs, mode=0o700, owner=(WORKER_UID, WORKER_GID))
    path = review_runs / run_id
    try:
        if path.resolve(strict=False).parent != review_runs.resolve(strict=True):
            raise OSError
    except OSError as error:
        raise SpeakerReviewObservationProcessingError("observation run invalid") from error
    return path


def _validate_submission_receipt(
    request: Mapping[str, object],
    preparation: PreparationBinding,
    snapshot: RunSnapshot,
    *,
    allow_partial_observation: bool,
) -> tuple[dict[str, object], str]:
    part_number = int(snapshot.state["primary_completed_part_count"])
    if snapshot.state["status"] in {"primary_submitted", "failed"}:
        part_number += 1
    suffix = "" if part_number == 1 else f".part-{part_number:04d}"
    path = SUBMISSION_RECEIPTS_ROOT / f"{request['run_id']}{suffix}.json"
    raw = _read_stable_file(
        path, maximum=ROOT_RECORD_MAX_BYTES, mode=0o600, owner=(ROOT_UID, ROOT_GID)
    )
    receipt = _decode_json(raw, canonical=True)
    if set(receipt) != _SUBMISSION_RECEIPT_KEYS:
        raise SpeakerReviewObservationProcessingError("submission receipt invalid")
    hashes = receipt.get("prepared_artifact_hashes")
    submit_authorization_id = receipt.get("authorization_id")
    if not isinstance(submit_authorization_id, str):
        raise SpeakerReviewObservationProcessingError("submission authorization invalid")
    submit_authorization_path = AUTHORIZATION_ROOT / f"{submit_authorization_id}.json"
    submit_authorization_raw = _read_stable_file(
        submit_authorization_path,
        maximum=submission.contract.REQUEST_MAX_BYTES,
        mode=0o600,
        owner=(ROOT_UID, ROOT_GID),
    )
    try:
        submit_authorization = submission.contract.parse_request(submit_authorization_raw)
    except (TypeError, ValueError) as error:
        raise SpeakerReviewObservationProcessingError("submission authorization invalid") from error
    expected_submit_authorization = {
        "archive_sha256": receipt.get("archive_sha256"),
        "authorization_id": submit_authorization_id,
        "maximum_authorized_cost_microusd": receipt.get("maximum_authorized_cost_microusd"),
        "operation": "submit_primary",
        "purpose": contract.PURPOSE,
        "run_id": receipt.get("run_id"),
        "schema_version": submission.contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
    }
    estimated_primary_cost_microusd = _cost_microusd(
        preparation.result["estimated_primary_cost_usd"]
    )
    if (
        receipt.get("schema_version") != 1
        or receipt.get("result") is None
        or receipt.get("archive_sha256") != request["archive_sha256"]
        or submit_authorization != expected_submit_authorization
        or receipt.get("authorization_sha256") != _sha256(submit_authorization_raw)
        or receipt.get("maximum_authorized_cost_microusd")
        != request["maximum_authorized_cost_microusd"]
        or receipt.get("run_id") != request["run_id"]
        or receipt.get("purpose") != contract.PURPOSE
        or receipt.get("season_number") != contract.SEASON_NUMBER
        or receipt.get("prep_receipt_sha256") != preparation.receipt_sha256
        or receipt.get("prepared_artifact_file_count") != preparation.artifact_file_count
        or receipt.get("prepared_artifact_set_sha256") != preparation.artifact_set_sha256
        or receipt.get("status") not in {"submitted", "already_submitted"}
        or receipt.get("operation") != "submit_primary"
        or type(receipt.get("estimated_primary_cost_microusd")) is not int
        or receipt["estimated_primary_cost_microusd"]
        != estimated_primary_cost_microusd
        or receipt["maximum_authorized_cost_microusd"]
        < estimated_primary_cost_microusd
        or receipt.get("primary_part_count")
        != preparation.result["primary_part_count"]
        or type(receipt.get("prepared_artifact_file_count")) is not int
        or receipt["prepared_artifact_file_count"] <= 0
        or not _is_sha256(receipt.get("prepared_artifact_set_sha256"))
        or type(receipt.get("post_artifact_file_count")) is not int
        or receipt["post_artifact_file_count"] <= 0
        or not _is_sha256(receipt.get("post_artifact_set_sha256"))
        or type(receipt.get("post_journal_file_count")) is not int
        or receipt["post_journal_file_count"] < 0
        or not _is_sha256(receipt.get("post_journal_set_sha256"))
        or not _is_sha256(receipt.get("post_run_state_sha256"))
        or not isinstance(hashes, dict)
        or set(hashes) != set(snapshot.base_hashes)
        or any(not _is_sha256(value) for value in hashes.values())
        or any(
            name != _RUN_STATE_FILENAME and hashes[name] != snapshot.base_hashes[name]
            for name in hashes
        )
    ):
        raise SpeakerReviewObservationProcessingError("submission receipt binding changed")
    result = receipt.get("result")
    try:
        checked_result = submission.contract.validate_aggregate(result)
    except (TypeError, ValueError) as error:
        raise SpeakerReviewObservationProcessingError("submission receipt invalid") from error
    if (
        checked_result["run_id"] != request["run_id"]
        or checked_result["primary_part_count"] != preparation.result["primary_part_count"]
        or checked_result["estimated_primary_cost_microusd"]
        != receipt["estimated_primary_cost_microusd"]
        or checked_result["status"] not in {"submitted", "already_submitted"}
        or checked_result["status"] != receipt["status"]
        or checked_result["submitted_part_count"] != 1
    ):
        raise SpeakerReviewObservationProcessingError("submission receipt invalid")
    if snapshot.journal_phase != "completed" or snapshot.state["status"] not in {
        "primary_submitted",
        "primary_part_completed",
        "failed",
    }:
        raise SpeakerReviewObservationProcessingError("submission evidence invalid")
    tolerated_outputs = (
        _part_output_names(part_number) | {"terminal-api-errors.jsonl"}
        if allow_partial_observation
        else frozenset()
    )
    submission_contents = {
        name: contents
        for name, contents in snapshot.all_contents.items()
        if name not in tolerated_outputs
    }
    if snapshot.state["status"] == "primary_submitted" and (
        receipt["post_artifact_file_count"] != len(submission_contents)
        or receipt["post_artifact_set_sha256"] != _set_digest(submission_contents)
        or receipt["post_journal_file_count"] != len(snapshot.journal_names)
        or receipt["post_journal_set_sha256"] != snapshot.journal_set_sha256
        or receipt["post_run_state_sha256"] != snapshot.base_hashes[_RUN_STATE_FILENAME]
    ):
        raise SpeakerReviewObservationProcessingError("submission post-evidence changed")
    return receipt, _sha256(raw)


def _read_run_snapshot(
    preparation: PreparationBinding,
    *,
    expected_hashes: Mapping[str, str] | None,
) -> RunSnapshot:
    run_directory = _run_directory(
        str(preparation.receipt["archive_sha256"]), str(preparation.result["run_id"])
    )
    _validate_directory(run_directory, mode=0o700, owner=(WORKER_UID, WORKER_GID))
    base_names = _expected_base_names(int(preparation.result["primary_part_count"]))
    try:
        names = {entry.name for entry in run_directory.iterdir()}
    except OSError as error:
        raise SpeakerReviewObservationProcessingError("observation run unavailable") from error
    all_output_names = {
        name
        for part in range(1, int(preparation.result["primary_part_count"]) + 1)
        for name in _part_output_names(part)
    } | {"terminal-api-errors.jsonl"}
    allowed = (
        base_names
        | {
            _WORKFLOW_INTENT_FILENAME,
            _WORKFLOW_COMPLETED_FILENAME,
        }
        | all_output_names
    )
    if not base_names <= names or not names <= allowed:
        raise SpeakerReviewObservationProcessingError("observation inventory invalid")
    base_contents: dict[str, bytes] = {}
    total_bytes = 0
    for name in sorted(base_names):
        per_file_maximum = (
            RUN_STATE_MAX_BYTES if name == _RUN_STATE_FILENAME else RUN_ARTIFACT_MAX_BYTES
        )
        raw = _read_stable_file(
            run_directory / name,
            maximum=min(per_file_maximum, RUN_ARTIFACT_TOTAL_MAX_BYTES - total_bytes),
            mode=0o600,
            owner=(WORKER_UID, WORKER_GID),
        )
        base_contents[name] = raw
        total_bytes += len(raw)
    state = _validate_run_state(
        _decode_json(base_contents[_RUN_STATE_FILENAME]), preparation=preparation
    )
    base_hashes = {name: _sha256(raw) for name, raw in base_contents.items()}
    if expected_hashes is not None:
        if set(expected_hashes) != base_names or any(
            name != _RUN_STATE_FILENAME and base_hashes[name] != expected_hashes[name]
            for name in base_names
        ):
            raise SpeakerReviewObservationProcessingError("prepared artifacts changed")
    journal_names = tuple(sorted(names & {_WORKFLOW_INTENT_FILENAME, _WORKFLOW_COMPLETED_FILENAME}))
    journal_contents: dict[str, bytes] = {}
    for name in journal_names:
        journal_contents[name] = _read_stable_file(
            run_directory / name,
            maximum=min(ROOT_RECORD_MAX_BYTES, RUN_ARTIFACT_TOTAL_MAX_BYTES - total_bytes),
            mode=0o600,
            owner=(WORKER_UID, WORKER_GID),
        )
        total_bytes += len(journal_contents[name])
    phase = _validate_journals(base_contents, journal_contents, state)
    observation_names = tuple(sorted(names & all_output_names))
    all_contents = {**base_contents, **journal_contents}
    for name in observation_names:
        all_contents[name] = _read_stable_file(
            run_directory / name,
            maximum=min(RUN_ARTIFACT_MAX_BYTES, RUN_ARTIFACT_TOTAL_MAX_BYTES - total_bytes),
            mode=0o600,
            owner=(WORKER_UID, WORKER_GID),
        )
        total_bytes += len(all_contents[name])
    return RunSnapshot(
        run_directory=run_directory,
        state=state,
        base_contents=base_contents,
        all_contents=all_contents,
        base_hashes=base_hashes,
        all_set_sha256=_set_digest(all_contents),
        journal_set_sha256=_set_digest(journal_contents),
        journal_names=journal_names,
        observation_names=observation_names,
        journal_phase=phase,
    )


def _validate_journals(
    base_contents: Mapping[str, bytes],
    journal_contents: Mapping[str, bytes],
    state: Mapping[str, object],
) -> str:
    if set(journal_contents) == {_WORKFLOW_INTENT_FILENAME, _WORKFLOW_COMPLETED_FILENAME}:
        part_number = int(state["primary_completed_part_count"])
        if state["status"] in {"primary_submitted", "failed"}:
            part_number += 1
        request_name = _PRIMARY_REQUEST_FILENAME_TEMPLATE.format(part_number=part_number)
        request_sha = _sha256(base_contents[request_name])
        try:
            intent = _decode_json(journal_contents[_WORKFLOW_INTENT_FILENAME], canonical=True)
            completed = _decode_json(journal_contents[_WORKFLOW_COMPLETED_FILENAME], canonical=True)
        except SpeakerReviewObservationProcessingError:
            raise SpeakerReviewObservationProcessingError("observation journal invalid") from None
        if (
            set(intent) != {"binding", "status"}
            or intent.get("status") != "intent"
            or set(completed) != {"binding", "batch_id", "input_file_id", "status"}
            or not _safe_text(completed.get("batch_id"))
            or not _safe_text(completed.get("input_file_id"))
        ):
            raise SpeakerReviewObservationProcessingError("observation journal invalid")
        ib = intent["binding"]
        cb = completed["binding"]
        if not isinstance(ib, dict) or ib != cb:
            raise SpeakerReviewObservationProcessingError("observation journal binding changed")
        if (
            ib.get("request_sha256") != request_sha
            or ib.get("run_id") != state["run_id"]
            or ib.get("stage") != "primary"
            or ib.get("part") != part_number
            or ib.get("prompt_version") != state["prompt_version"]
            or set(ib)
            != {
                "batch_endpoint",
                "completion_window",
                "part",
                "prompt_version",
                "request_sha256",
                "run_id",
                "schema_version",
                "stage",
            }
            or ib.get("schema_version") != 1
            or not _safe_text(ib.get("batch_endpoint"), 128)
            or not _safe_text(ib.get("completion_window"), 64)
            or not _safe_text(completed.get("status"), 128)
        ):
            raise SpeakerReviewObservationProcessingError("observation journal binding changed")
        if state["status"] in {"primary_submitted", "primary_part_completed", "failed"} and (
            state["primary_batch_id"] != completed["batch_id"]
            or state["primary_input_file_id"] != completed["input_file_id"]
        ):
            raise SpeakerReviewObservationProcessingError("observation state binding changed")
        return "completed"
    if journal_contents:
        raise SpeakerReviewObservationProcessingError("observation journal inventory invalid")
    return "none"


def _root_intent_path(run_id: str, part_number: int = 1) -> Path:
    suffix = "" if part_number == 1 else f".part-{part_number:04d}"
    return OBSERVATION_RECEIPTS_ROOT / f"{run_id}{suffix}.intent.json"


def _root_receipt_path(run_id: str, part_number: int = 1) -> Path:
    suffix = "" if part_number == 1 else f".part-{part_number:04d}"
    return OBSERVATION_RECEIPTS_ROOT / f"{run_id}{suffix}.json"


def _root_intent(
    request: Mapping[str, object],
    *,
    authorization_sha256: str,
    preparation: PreparationBinding,
    submission_receipt_sha256: str,
    snapshot: RunSnapshot,
) -> dict[str, object]:
    evidence = {
        name: contents
        for name, contents in snapshot.all_contents.items()
        if name != _RUN_STATE_FILENAME
    }
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
        "prepared_artifact_hashes": dict(sorted(snapshot.base_hashes.items())),
        "primary_part_count": preparation.result["primary_part_count"],
        "primary_part_number": snapshot.state["primary_completed_part_count"] + 1,
        "pre_observation_primary_completed_part_count": snapshot.state[
            "primary_completed_part_count"
        ],
        "pre_observation_observation_names": list(snapshot.observation_names),
        "pre_observation_evidence_file_count": len(evidence),
        "pre_observation_evidence_set_sha256": _set_digest(evidence),
        "pre_observation_state_binding_sha256": _state_binding_sha256(snapshot.state),
        "pre_observation_run_state_sha256": snapshot.base_hashes[_RUN_STATE_FILENAME],
        "purpose": contract.PURPOSE,
        "run_id": preparation.result["run_id"],
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "season_number": contract.SEASON_NUMBER,
        "status": "intent",
        "submission_receipt_sha256": submission_receipt_sha256,
    }


def _read_root_record(path: Path) -> dict[str, object]:
    _repair_linked_publication(path)
    raw = _read_stable_file(
        path, maximum=ROOT_RECORD_MAX_BYTES, mode=0o600, owner=(ROOT_UID, ROOT_GID)
    )
    return _decode_json(raw, canonical=True)


def _staging_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.pending")


def _fsync_receipt_directory() -> None:
    if os.name != "posix":
        return
    directory = os.open(
        OBSERVATION_RECEIPTS_ROOT,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _repair_linked_publication(path: Path) -> None:
    staging = _staging_path(path)
    if not os.path.lexists(staging):
        return
    try:
        final_metadata = path.lstat()
        staging_metadata = staging.lstat()
        if (
            not stat.S_ISREG(final_metadata.st_mode)
            or stat.S_ISLNK(final_metadata.st_mode)
            or not stat.S_ISREG(staging_metadata.st_mode)
            or stat.S_ISLNK(staging_metadata.st_mode)
            or (final_metadata.st_dev, final_metadata.st_ino)
            != (staging_metadata.st_dev, staging_metadata.st_ino)
            or final_metadata.st_nlink != 2
            or staging_metadata.st_nlink != 2
            or (os.name == "posix" and stat.S_IMODE(final_metadata.st_mode) != 0o600)
            or (
                os.name == "posix"
                and (final_metadata.st_uid, final_metadata.st_gid) != (ROOT_UID, ROOT_GID)
            )
        ):
            raise OSError
        os.unlink(staging)
        _fsync_receipt_directory()
    except OSError as error:
        raise SpeakerReviewObservationProcessingError(
            "observation receipt publication invalid"
        ) from error


def _remove_incomplete_staging(staging: Path) -> None:
    try:
        metadata = staging.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_nlink != 1
            or (os.name == "posix" and stat.S_IMODE(metadata.st_mode) != 0o600)
            or (
                os.name == "posix"
                and (metadata.st_uid, metadata.st_gid) != (ROOT_UID, ROOT_GID)
            )
        ):
            raise OSError
        os.unlink(staging)
        _fsync_receipt_directory()
    except OSError as error:
        raise SpeakerReviewObservationProcessingError(
            "observation receipt publication invalid"
        ) from error


def _write_once(path: Path, value: Mapping[str, object]) -> None:
    encoded = _canonical_json(value)
    if len(encoded) > ROOT_RECORD_MAX_BYTES:
        raise SpeakerReviewObservationProcessingError("observation receipt too large")
    _validate_directory(OBSERVATION_RECEIPTS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    staging = _staging_path(path)
    if os.path.lexists(path):
        _repair_linked_publication(path)
        raise SpeakerReviewObservationProcessingError("observation receipt conflict")
    if os.path.lexists(staging):
        try:
            existing = _read_stable_file(
                staging,
                maximum=ROOT_RECORD_MAX_BYTES,
                mode=0o600,
                owner=(ROOT_UID, ROOT_GID),
            )
        except SpeakerReviewObservationProcessingError:
            _remove_incomplete_staging(staging)
        else:
            if existing != encoded:
                try:
                    _decode_json(existing, canonical=True)
                except SpeakerReviewObservationProcessingError:
                    _remove_incomplete_staging(staging)
                else:
                    raise SpeakerReviewObservationProcessingError(
                        "observation receipt conflict"
                    )
    descriptor = -1
    try:
        if not os.path.lexists(staging):
            descriptor = os.open(
                staging,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            _fsync_receipt_directory()
        os.link(staging, path, follow_symlinks=False)
        _fsync_receipt_directory()
        os.unlink(staging)
        _fsync_receipt_directory()
    except FileExistsError as error:
        raise SpeakerReviewObservationProcessingError("observation receipt conflict") from error
    except OSError as error:
        raise SpeakerReviewObservationProcessingError("observation receipt unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _aggregate(snapshot: RunSnapshot, *, status: str) -> dict[str, object]:
    result = {
        "estimated_primary_cost_microusd": _cost_microusd(
            snapshot.state["estimated_primary_cost_usd"]
        ),
        "operation": contract.OPERATION,
        "primary_completed_part_count": snapshot.state["primary_completed_part_count"],
        "primary_part_count": snapshot.state["primary_part_count"],
        "purpose": contract.PURPOSE,
        "run_id": snapshot.state["run_id"],
        "run_status": snapshot.state["status"],
        "season_number": contract.SEASON_NUMBER,
        "status": status,
    }
    try:
        return contract.validate_aggregate(result, status=status)
    except (TypeError, ValueError) as error:
        raise SpeakerReviewObservationProcessingError("observation aggregate invalid") from error


def _validate_intent(value: object, expected: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _INTENT_KEYS or value != dict(expected):
        raise SpeakerReviewObservationProcessingError("observation intent binding changed")
    hashes = value.get("prepared_artifact_hashes")
    if not isinstance(hashes, dict) or any(not _is_sha256(item) for item in hashes.values()):
        raise SpeakerReviewObservationProcessingError("observation intent invalid")
    return dict(value)


def _validate_existing_intent(
    value: object,
    *,
    request: Mapping[str, object],
    preparation: PreparationBinding,
    authorization_sha256: str,
    submission_receipt_sha256: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _INTENT_KEYS:
        raise SpeakerReviewObservationProcessingError("observation intent invalid")
    expected = {
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
        "primary_part_count": preparation.result["primary_part_count"],
        "purpose": contract.PURPOSE,
        "run_id": request["run_id"],
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "season_number": contract.SEASON_NUMBER,
        "status": "intent",
        "submission_receipt_sha256": submission_receipt_sha256,
    }
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise SpeakerReviewObservationProcessingError("observation intent binding changed")
    hashes = value.get("prepared_artifact_hashes")
    if (
        not isinstance(hashes, dict)
        or set(hashes) != _expected_base_names(int(preparation.result["primary_part_count"]))
        or any(not _is_sha256(item) for item in hashes.values())
    ):
        raise SpeakerReviewObservationProcessingError("observation intent invalid")
    if (
        type(value.get("pre_observation_primary_completed_part_count")) is not int
        or value["pre_observation_primary_completed_part_count"] < 0
        or value["pre_observation_primary_completed_part_count"]
        >= preparation.result["primary_part_count"]
        or not _is_sha256(value.get("pre_observation_state_binding_sha256"))
        or not _is_sha256(value.get("pre_observation_run_state_sha256"))
    ):
        raise SpeakerReviewObservationProcessingError("observation intent invalid")
    if (
        type(value.get("primary_part_number")) is not int
        or value["primary_part_number"]
        != value["pre_observation_primary_completed_part_count"] + 1
    ):
        raise SpeakerReviewObservationProcessingError("observation intent invalid")
    prior_outputs = value.get("pre_observation_observation_names")
    all_outputs = {
        name
        for part in range(1, int(preparation.result["primary_part_count"]) + 1)
        for name in _part_output_names(part)
    } | {"terminal-api-errors.jsonl"}
    if (
        not isinstance(prior_outputs, list)
        or any(item not in all_outputs for item in prior_outputs)
        or len(set(prior_outputs)) != len(prior_outputs)
    ):
        raise SpeakerReviewObservationProcessingError("observation intent invalid")
    if (
        type(value.get("pre_observation_evidence_file_count")) is not int
        or value["pre_observation_evidence_file_count"] <= 0
        or not _is_sha256(value.get("pre_observation_evidence_set_sha256"))
        or value["pre_observation_evidence_file_count"]
        != len(_expected_base_names(int(preparation.result["primary_part_count"])))
        - 1
        + 2
        + len(prior_outputs)
    ):
        raise SpeakerReviewObservationProcessingError("observation intent invalid")
    return dict(value)


def _active_evidence_additions(
    snapshot: RunSnapshot,
    intent: Mapping[str, object],
) -> set[str]:
    prior_outputs = intent.get("pre_observation_observation_names")
    if not isinstance(prior_outputs, list):
        raise SpeakerReviewObservationProcessingError("observation intent invalid")
    current_outputs = set(snapshot.observation_names)
    prior_names = set(prior_outputs)
    if not prior_names <= current_outputs:
        raise SpeakerReviewObservationProcessingError("observation evidence changed")
    baseline_names = (
        (set(snapshot.base_contents) - {_RUN_STATE_FILENAME})
        | set(snapshot.journal_names)
        | prior_names
    )
    if not baseline_names <= set(snapshot.all_contents):
        raise SpeakerReviewObservationProcessingError("observation evidence changed")
    baseline = {name: snapshot.all_contents[name] for name in baseline_names}
    if (
        len(baseline) != intent["pre_observation_evidence_file_count"]
        or _set_digest(baseline) != intent["pre_observation_evidence_set_sha256"]
    ):
        raise SpeakerReviewObservationProcessingError("observation pre-evidence changed")
    return current_outputs - prior_names


def _validate_transition(
    before: RunSnapshot,
    after: RunSnapshot,
    result: Mapping[str, object],
    intent: Mapping[str, object],
) -> None:
    if after.journal_phase != "completed" or after.state["run_id"] != before.state["run_id"]:
        raise SpeakerReviewObservationProcessingError("observation post-state invalid")
    if (
        _state_binding_sha256(after.state) != intent["pre_observation_state_binding_sha256"]
        or result["primary_part_count"] != after.state["primary_part_count"]
        or result["estimated_primary_cost_microusd"] != intent["estimated_primary_cost_microusd"]
    ):
        raise SpeakerReviewObservationProcessingError("observation post-state invalid")
    if after.state["primary_part_count"] != before.state["primary_part_count"]:
        raise SpeakerReviewObservationProcessingError("observation post-state invalid")
    before_count = int(intent["pre_observation_primary_completed_part_count"])
    if (
        before.state["primary_completed_part_count"] != before_count
        or before.base_hashes[_RUN_STATE_FILENAME]
        != intent["pre_observation_run_state_sha256"]
    ):
        raise SpeakerReviewObservationProcessingError("observation pre-state changed")
    before_added = _active_evidence_additions(before, intent)
    after_added = _active_evidence_additions(after, intent)
    if not before_added <= after_added or any(
        before.all_contents[name] != after.all_contents[name] for name in before_added
    ):
        raise SpeakerReviewObservationProcessingError("observation pre-evidence changed")
    active_outputs = _part_output_names(int(intent["primary_part_number"]))
    if before_added - (
        active_outputs | {"terminal-api-errors.jsonl"}
    ) or after_added - (active_outputs | {"terminal-api-errors.jsonl"}):
        raise SpeakerReviewObservationProcessingError("observation post-state invalid")
    if result["status"] == "waiting":
        if (
            after.state != before.state
            or after.all_set_sha256 != before.all_set_sha256
            or not before_added <= active_outputs
            or result["run_status"] != "primary_submitted"
        ):
            raise SpeakerReviewObservationProcessingError("observation post-state invalid")
    elif result["status"] == "reconciliation_required":
        if (
            after.state != before.state
            or not after_added <= active_outputs
            or result["run_status"] != "primary_submitted"
        ):
            raise SpeakerReviewObservationProcessingError("observation post-state invalid")
    elif result["status"] == "observed":
        if (
            after.state["status"] != "primary_part_completed"
            or after.state["primary_completed_part_count"] != before_count + 1
            or f"primary-part-{int(intent['primary_part_number']):04d}-output.jsonl"
            not in after.observation_names
            or not after_added <= active_outputs
        ):
            raise SpeakerReviewObservationProcessingError("observation post-state invalid")
    elif result["status"] == "failed":
        if (
            after.state["status"] != "failed"
            or after.state["primary_completed_part_count"] != before_count
            or not after_added <= {"terminal-api-errors.jsonl"}
        ):
            raise SpeakerReviewObservationProcessingError("observation post-state invalid")
    else:
        raise SpeakerReviewObservationProcessingError("observation worker result invalid")
    if (
        result["run_status"] != after.state["status"]
        or result["primary_completed_part_count"] != after.state["primary_completed_part_count"]
    ):
        raise SpeakerReviewObservationProcessingError("observation worker result invalid")


def _validate_recovered_transition(
    snapshot: RunSnapshot,
    result: Mapping[str, object],
    intent: Mapping[str, object],
) -> None:
    if (
        _state_binding_sha256(snapshot.state) != intent["pre_observation_state_binding_sha256"]
        or result["primary_part_count"] != snapshot.state["primary_part_count"]
        or result["estimated_primary_cost_microusd"] != intent["estimated_primary_cost_microusd"]
    ):
        raise SpeakerReviewObservationProcessingError("observation post-state invalid")
    before_count = int(intent["pre_observation_primary_completed_part_count"])
    added = _active_evidence_additions(snapshot, intent)
    active_outputs = _part_output_names(int(intent["primary_part_number"]))
    if result["status"] == "observed":
        valid = (
            snapshot.state["status"] == "primary_part_completed"
            and snapshot.state["primary_completed_part_count"] == before_count + 1
            and f"primary-part-{int(intent['primary_part_number']):04d}-output.jsonl" in added
            and added <= active_outputs
        )
    elif result["status"] == "failed":
        valid = (
            snapshot.state["status"] == "failed"
            and snapshot.state["primary_completed_part_count"] == before_count
            and added <= {"terminal-api-errors.jsonl"}
        )
    else:
        valid = False
    if (
        not valid
        or result["run_status"] != snapshot.state["status"]
        or result["primary_completed_part_count"] != snapshot.state["primary_completed_part_count"]
    ):
        raise SpeakerReviewObservationProcessingError("observation post-state invalid")


def _validate_final_receipt(
    value: object,
    *,
    intent: Mapping[str, object],
    snapshot: RunSnapshot,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _RECEIPT_KEYS:
        raise SpeakerReviewObservationProcessingError("observation receipt invalid")
    for key, expected in intent.items():
        if key != "status" and value.get(key) != expected:
            raise SpeakerReviewObservationProcessingError("observation receipt binding changed")
    result = value.get("result")
    try:
        checked = contract.validate_aggregate(result)
    except (TypeError, ValueError) as error:
        raise SpeakerReviewObservationProcessingError("observation receipt invalid") from error
    if (
        value.get("post_artifact_file_count") != len(snapshot.all_contents)
        or value.get("post_artifact_set_sha256") != snapshot.all_set_sha256
        or value.get("post_journal_file_count") != len(snapshot.journal_names)
        or value.get("post_journal_set_sha256") != snapshot.journal_set_sha256
        or value.get("post_run_state_sha256") != _sha256(snapshot.all_contents[_RUN_STATE_FILENAME])
        or checked["status"] not in {"observed", "failed"}
        or value.get("status") != checked["status"]
    ):
        raise SpeakerReviewObservationProcessingError("observation receipt invalid")
    _validate_recovered_transition(snapshot, checked, intent)
    return checked


def _receipt_payload(
    intent: Mapping[str, object], result: Mapping[str, object], snapshot: RunSnapshot
) -> dict[str, object]:
    return {
        **dict(intent),
        "status": result["status"],
        "post_artifact_file_count": len(snapshot.all_contents),
        "post_artifact_set_sha256": snapshot.all_set_sha256,
        "post_journal_file_count": len(snapshot.journal_names),
        "post_journal_set_sha256": snapshot.journal_set_sha256,
        "post_run_state_sha256": _sha256(snapshot.all_contents[_RUN_STATE_FILENAME]),
        "result": dict(result),
    }


def _safe_compose_environment() -> dict[str, str]:
    return {
        "PATH": "/usr/sbin:/usr/bin",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _container_identity_is_exact(review_runs: Path) -> bool:
    """Prove the fixed name resolves to this release's one-shot container."""

    template = (
        '{{.Name}}\n{{index .Config.Labels "com.docker.compose.service"}}\n'
        '{{index .Config.Labels "com.docker.compose.oneoff"}}\n'
        '{{index .Config.Labels "com.docker.compose.project.config_files"}}\n'
        '{{index .Config.Labels "com.docker.compose.project.working_dir"}}\n'
        '{{.Config.Image}}\n'
        '{{range .Mounts}}{{printf "%s|%s|%t\\n" .Source .Destination .RW}}{{end}}'
    )
    try:
        expected_image = _release_image_reference()
        inspected = subprocess.run(
            ["docker", "inspect", "--format", template, CONTAINER_NAME],
            cwd=RELEASE_ROOT,
            env=_safe_compose_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            check=False,
            timeout=WORKER_KILL_AFTER_SECONDS,
        )
    except Exception:
        return False
    output = inspected.stdout
    if isinstance(output, str):
        output = output.encode()
    try:
        lines = output.decode("utf-8").splitlines()
    except UnicodeError:
        return False
    expected_header = [
        f"/{CONTAINER_NAME}",
        COMPOSE_SERVICE,
        "True",
        os.fspath(COMPOSE_PATH),
        os.fspath(RELEASE_ROOT),
        expected_image,
    ]
    expected_mount = f"{review_runs.as_posix()}|{WORKER_MOUNT}|true"
    return (
        inspected.returncode == 0
        and len(lines) >= len(expected_header) + 1
        and lines[: len(expected_header)] == expected_header
        and lines[len(expected_header) :].count(expected_mount) == 1
    )


def _worker_arguments(request: Mapping[str, object], review_runs: Path) -> list[str]:
    arguments = [
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
    ]
    expected = (
        (contract.ENV_EXPECTED_PRIMARY_PART_NUMBER, request.get("_expected_primary_part_number")),
        (contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256, request.get("_expected_pre_run_state_sha256")),
        (
            contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256,
            request.get("_expected_pre_artifact_set_sha256"),
        ),
        (
            contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256,
            request.get("_expected_pre_journal_set_sha256"),
        ),
        (contract.ENV_EXPECTED_REQUEST_SHA256, request.get("_expected_request_sha256")),
    )
    for name, value in expected:
        if value is not None:
            arguments.extend(["--env", f"{name}={value}"])
    arguments.append(COMPOSE_SERVICE)
    return arguments


def _cleanup_compose_worker(review_runs: Path) -> None:
    if not _container_identity_is_exact(review_runs):
        return
    try:
        subprocess.run(
            ["docker", "rm", "--force", CONTAINER_NAME],
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
        return


def _terminate_worker(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                pass
            try:
                process.wait(timeout=WORKER_KILL_AFTER_SECONDS)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
                process.wait()
        else:
            process.kill()
            process.wait()


def _run_worker(request: Mapping[str, object], review_runs: Path) -> dict[str, object]:
    process: subprocess.Popen[bytes] | None = None
    _cleanup_compose_worker(review_runs)
    try:
        process = subprocess.Popen(
            _worker_arguments(request, review_runs),
            cwd=RELEASE_ROOT,
            env=_safe_compose_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=os.name == "posix",
        )
        if process.stdout is None or process.stderr is None:
            raise SpeakerReviewObservationProcessingError("observation worker failed")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            stdout_future = executor.submit(
                lambda: process.stdout.read(WORKER_OUTPUT_MAX_BYTES + 1)
            )
            stderr_future = executor.submit(
                lambda: process.stderr.read(WORKER_OUTPUT_MAX_BYTES + 1)
            )
            try:
                code = process.wait(timeout=WORKER_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as error:
                _terminate_worker(process)
                stdout_future.result(timeout=5)
                stderr_future.result(timeout=5)
                raise SpeakerReviewObservationProcessingError(
                    "observation worker failed"
                ) from error
            stdout, stderr = stdout_future.result(timeout=5), stderr_future.result(timeout=5)
        if code != 0 or stderr or len(stdout) > WORKER_OUTPUT_MAX_BYTES:
            raise SpeakerReviewObservationProcessingError("observation worker failed")
        try:
            return contract.parse_aggregate(stdout)
        except (TypeError, ValueError) as error:
            raise SpeakerReviewObservationProcessingError("observation worker failed") from error
    except SpeakerReviewObservationProcessingError:
        raise
    except (OSError, subprocess.SubprocessError, TimeoutError) as error:
        raise SpeakerReviewObservationProcessingError("observation worker failed") from error
    finally:
        if process is not None and process.poll() is None:
            try:
                _terminate_worker(process)
            except (OSError, subprocess.SubprocessError):
                pass
        _cleanup_compose_worker(review_runs)


def process_request(request: Mapping[str, object]) -> dict[str, object]:
    try:
        request = contract.validate_request(request)
    except (TypeError, ValueError) as error:
        raise SpeakerReviewObservationProcessingError("invalid observation request") from error
    authorization_sha256 = _validate_authorization(request)
    preparation = _validate_preparation_receipt(request)
    _validate_directory(OBSERVATION_RECEIPTS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    run = _read_run_snapshot(preparation, expected_hashes=None)
    part_number = int(run.state["primary_completed_part_count"])
    if run.state["status"] in {"primary_submitted", "failed"}:
        part_number += 1
    if part_number != 1:
        raise SpeakerReviewObservationProcessingError("observation pre-state invalid")
    intent_path, receipt_path = (
        _root_intent_path(str(request["run_id"]), part_number),
        _root_receipt_path(str(request["run_id"]), part_number),
    )
    intent_exists = os.path.lexists(intent_path)
    _, submission_sha256 = _validate_submission_receipt(
        request,
        preparation,
        run,
        allow_partial_observation=intent_exists,
    )
    if not intent_exists and run.observation_names:
        raise SpeakerReviewObservationProcessingError("observation pre-evidence changed")
    if os.path.lexists(receipt_path):
        if not intent_exists:
            raise SpeakerReviewObservationProcessingError("orphan observation receipt")
        intent = _validate_existing_intent(
            _read_root_record(intent_path),
            request=request,
            preparation=preparation,
            authorization_sha256=authorization_sha256,
            submission_receipt_sha256=submission_sha256,
        )
        checked = _validate_final_receipt(
            _read_root_record(receipt_path), intent=intent, snapshot=run
        )
        return {
            **checked,
            "status": "already_observed" if checked["status"] == "observed" else "failed",
        }
    if intent_exists:
        intent = _validate_existing_intent(
            _read_root_record(intent_path),
            request=request,
            preparation=preparation,
            authorization_sha256=authorization_sha256,
            submission_receipt_sha256=submission_sha256,
        )
    else:
        if run.state["status"] != "primary_submitted" or run.journal_phase != "completed":
            raise SpeakerReviewObservationProcessingError("observation pre-state invalid")
        intent = _root_intent(
            request,
            authorization_sha256=authorization_sha256,
            preparation=preparation,
            submission_receipt_sha256=submission_sha256,
            snapshot=run,
        )
        _write_once(intent_path, intent)
    expected_hashes = intent["prepared_artifact_hashes"]
    before = _read_run_snapshot(preparation, expected_hashes=expected_hashes)
    if before.state["status"] in {"primary_part_completed", "failed"}:
        after = before
        status = "observed" if after.state["status"] == "primary_part_completed" else "failed"
        result = _aggregate(after, status=status)
        _validate_recovered_transition(after, result, intent)
    else:
        if before.state["status"] != "primary_submitted":
            raise SpeakerReviewObservationProcessingError("observation pre-state invalid")
        worker_request = {
            **request,
            "_expected_primary_part_number": str(intent["primary_part_number"]),
            "_expected_pre_run_state_sha256": intent["pre_observation_run_state_sha256"],
            "_expected_pre_artifact_set_sha256": _set_digest(
                {
                    name: raw
                    for name, raw in before.all_contents.items()
                    if not name.startswith(".primary-part-")
                }
            ),
            "_expected_pre_journal_set_sha256": before.journal_set_sha256,
            "_expected_request_sha256": _sha256(
                before.base_contents[
                    _PRIMARY_REQUEST_FILENAME_TEMPLATE.format(
                        part_number=int(intent["primary_part_number"])
                    )
                ]
            ),
        }
        worker_result = _run_worker(worker_request, before.run_directory.parent)
        if (
            worker_result["run_id"] != request["run_id"]
            or worker_result["estimated_primary_cost_microusd"]
            != intent["estimated_primary_cost_microusd"]
            or worker_result["primary_part_count"] != intent["primary_part_count"]
        ):
            raise SpeakerReviewObservationProcessingError("observation worker result invalid")
        after = _read_run_snapshot(preparation, expected_hashes=expected_hashes)
        _validate_transition(before, after, worker_result, intent)
        result = _aggregate(after, status=str(worker_result["status"]))
        if result["status"] in {"waiting", "reconciliation_required"}:
            return result
    receipt = _receipt_payload(intent, result, after)
    _write_once(receipt_path, receipt)
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
        raise SpeakerReviewObservationProcessingError("invalid observation caller")


def main() -> int:
    try:
        _require_root_context()
        result = process_request(_read_request(sys.stdin.buffer))
        sys.stdout.buffer.write(contract.canonical_json(result))
        return 0
    except Exception:
        sys.stderr.write("error=speaker_review_observation_rejected\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
