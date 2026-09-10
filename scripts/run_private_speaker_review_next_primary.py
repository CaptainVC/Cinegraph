"""Root-only coordinator for one bounded primary-part transition.

This boundary is intentionally narrower than the workflow.  It is allowed to
submit exactly primary part two after a root-verified observation of part one;
it cannot observe output, adjudicate, finalize, or ingest.  All durable root
records contain hashes and runtime bindings so a retry can repair a crash
without issuing another provider request.
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
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import BinaryIO, Final, Mapping

_SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if os.fspath(_SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, os.fspath(_SCRIPT_DIRECTORY))

try:
    from scripts import (
        private_speaker_review_next_primary_host_contract as host,
    )
    from scripts import (
        private_speaker_review_next_primary_submission_contract as contract,
    )
    from scripts import (
        run_private_speaker_review_observation as observation,
    )
    from scripts import (
        run_private_speaker_review_submission as submission,
    )
except ModuleNotFoundError:
    import private_speaker_review_next_primary_host_contract as host
    import private_speaker_review_next_primary_submission_contract as contract
    import run_private_speaker_review_observation as observation
    import run_private_speaker_review_submission as submission


class NextPrimaryProcessingError(RuntimeError):
    """Generic rejection; no private/provider detail crosses the boundary."""


RELEASE_ROOT: Final = Path(__file__).resolve().parents[1]
SPEAKER_REVIEW_ROOT: Final = host.SPEAKER_REVIEW_ROOT
PREPARATION_RECEIPTS_ROOT: Final = SPEAKER_REVIEW_ROOT / "receipts"
AUTHORIZATION_ROOT: Final = host.REVIEW_AUTHORIZATION_ROOT
SUBMISSION_RECEIPTS_ROOT: Final = host.REVIEW_SUBMISSION_RECEIPTS_ROOT
OBSERVATION_RECEIPTS_ROOT: Final = host.REVIEW_OBSERVATION_RECEIPTS_ROOT
NEXT_RECEIPTS_ROOT: Final = host.REVIEW_NEXT_PRIMARY_RECEIPTS_ROOT
REVIEW_RUNS_ROOT: Final = host.SPEAKER_REVIEW_RUNS_ROOT
DEV_ENV_FILE: Final = host.ENV_FILE
COMPOSE_PATH: Final = RELEASE_ROOT / "deploy/compose.yaml"
WORKER_MOUNT: Final = "/review-workspace/review-runs"
ROOT_UID: Final = 0
ROOT_GID: Final = 0
WORKER_UID: Final = host.UID_IN_CONTAINER
WORKER_GID: Final = host.GID_IN_CONTAINER
ROOT_RECORD_MAX_BYTES: Final = 128 * 1024
RUN_STATE_MAX_BYTES: Final = 64 * 1024
RUN_ARTIFACT_MAX_BYTES: Final = 64 * 1024 * 1024
RUN_ARTIFACT_TOTAL_MAX_BYTES: Final = 256 * 1024 * 1024

_STATE = "run-state.json"
_CANDIDATES = "candidates.jsonl"
_MANIFEST = "source-manifest.json"
_REQUEST = "primary-part-{part:04d}-requests.jsonl"
_INTENT = ".primary-part-{part:04d}-submission-intent.json"
_COMPLETED = ".primary-part-{part:04d}-submission-completed.json"
_RECEIPT_SCHEMA = 1
_IMAGE_NAME = "ghcr.io/captainvc/cinegraph"
_EXPECTED_REQUEST_KEY = "_expected_request_sha256"
_EXPECTED_PRE_ARTIFACTS_KEY = "_expected_pre_artifact_set_sha256"
_EXPECTED_PRE_JOURNALS_KEY = "_expected_pre_journal_set_sha256"
_EXPECTED_PRE_STATE_KEY = "_expected_pre_run_state_sha256"
_BINDING_KEYS = frozenset(
    {
        "archive_sha256",
        "authorization_id",
        "authorization_sha256",
        "configuration_sha256",
        "estimated_primary_cost_microusd",
        "first_submission_receipt_sha256",
        "observation_receipt_sha256",
        "operation",
        "prep_receipt_sha256",
        "prepared_artifact_hashes",
        "primary_part_count",
        "primary_part_number",
        "purpose",
        "release_sha",
        "image_reference",
        "run_id",
        "schema_version",
        "season_number",
        "maximum_authorized_cost_microusd",
        "request_sha256",
        "pre_artifact_set_sha256",
        "pre_journal_set_sha256",
        "pre_run_state_sha256",
        "status",
    }
)
_RECEIPT_KEYS = frozenset(
    {
        *_BINDING_KEYS,
        "post_artifact_set_sha256",
        "post_journal_set_sha256",
        "post_run_state_sha256",
        "post_artifact_file_count",
        "status",
        "result",
    }
)


def _canonical(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_sha(value: object) -> bool:
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )


def _cost_microusd(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NextPrimaryProcessingError("next-primary cost invalid")
    if not math.isfinite(float(value)) or float(value) < 0:
        raise NextPrimaryProcessingError("next-primary cost invalid")
    try:
        micros = Decimal(str(value)) * Decimal(1_000_000)
    except (InvalidOperation, ValueError) as error:
        raise NextPrimaryProcessingError("next-primary cost invalid") from error
    if micros != micros.to_integral_value() or micros < 0:
        raise NextPrimaryProcessingError("next-primary cost invalid")
    result = int(micros)
    if result > contract.MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise NextPrimaryProcessingError("next-primary cost invalid")
    return result


def _safe_text(value: object, *, maximum: int = 512) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and len(value) <= maximum
        and all(ord(character) >= 32 for character in value)
    )


def _read_request(stream: BinaryIO) -> dict[str, object]:
    raw = stream.readline(contract.REQUEST_MAX_BYTES + 1)
    if stream.read(1):
        raise NextPrimaryProcessingError("invalid next-primary request")
    try:
        return contract.parse_request(raw)
    except (TypeError, ValueError) as error:
        raise NextPrimaryProcessingError("invalid next-primary request") from error


def _stable(path: Path, *, maximum: int, mode: int, owner: tuple[int, int]) -> bytes:
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
            raw = os.read(descriptor, maximum + 1)
        finally:
            os.close(descriptor)
        after = path.lstat()

        def identity(value: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
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

        if (
            identity(before) != identity(opened)
            or identity(opened) != identity(after)
            or len(raw) != opened.st_size
        ):
            raise OSError
        return raw
    except OSError as error:
        raise NextPrimaryProcessingError("next-primary evidence unavailable") from error


def _decode(raw: bytes, *, maximum: int = ROOT_RECORD_MAX_BYTES) -> dict[str, object]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise NextPrimaryProcessingError("next-primary evidence invalid") from error
    if not isinstance(value, dict) or _canonical(value) != raw or len(raw) > maximum:
        raise NextPrimaryProcessingError("next-primary evidence invalid")
    return value


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _directory(path: Path, *, mode: int, owner: tuple[int, int]) -> None:
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or (os.name == "posix" and stat.S_IMODE(metadata.st_mode) != mode)
            or (os.name == "posix" and (metadata.st_uid, metadata.st_gid) != owner)
            or path.resolve(strict=True) != path
        ):
            raise OSError
    except OSError as error:
        raise NextPrimaryProcessingError("next-primary evidence unavailable") from error


def _read_record(path: Path) -> tuple[dict[str, object], str]:
    raw = _stable(path, maximum=ROOT_RECORD_MAX_BYTES, mode=0o600, owner=(ROOT_UID, ROOT_GID))
    return _decode(raw), _sha(raw)


def _active_binding() -> tuple[str, str, str]:
    try:
        binding = submission._active_runtime_binding()
        inspected = subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                binding[1],
                "--format",
                "{{json .Config.Labels}}",
            ],
            cwd=RELEASE_ROOT,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            check=False,
            timeout=10,
        )
        if (
            inspected.returncode != 0
            or inspected.stderr
            or len(inspected.stdout) > contract.OUTPUT_MAX_BYTES
        ):
            raise ValueError
        labels = json.loads(
            inspected.stdout.decode("utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if not isinstance(labels, dict) or (
            labels.get(host.CINEGRAPH_IMAGE_REVISION_LABEL) != binding[0]
            or labels.get(host.CINEGRAPH_IMAGE_SOURCE_LABEL) != host.CINEGRAPH_IMAGE_SOURCE
            or labels.get(host.CINEGRAPH_IMAGE_VERSION_LABEL) != f"sha-{binding[0]}"
        ):
            raise ValueError
        return binding
    except (
        OSError,
        subprocess.SubprocessError,
        TimeoutError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
        submission.SpeakerReviewSubmissionProcessingError,
    ) as error:
        raise NextPrimaryProcessingError("active runtime unavailable") from error


def _validate_authorization(request: Mapping[str, object]) -> str:
    _directory(AUTHORIZATION_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    raw = _stable(
        AUTHORIZATION_ROOT / f"{request['authorization_id']}.json",
        maximum=contract.REQUEST_MAX_BYTES,
        mode=0o600,
        owner=(ROOT_UID, ROOT_GID),
    )
    try:
        if contract.parse_request(raw) != dict(request):
            raise ValueError
    except (TypeError, ValueError) as error:
        raise NextPrimaryProcessingError("next-primary authorization invalid") from error
    return _sha(raw)


def _validate_preparation(request: Mapping[str, object]) -> tuple[dict[str, object], str]:
    digest = str(request["archive_sha256"])
    _directory(PREPARATION_RECEIPTS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    receipt, receipt_sha = _read_record(PREPARATION_RECEIPTS_ROOT / f"sha256-{digest}.json")
    expected_keys = {
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
    active_sha, image, config_sha = _active_binding()
    result = receipt.get("result")
    if (
        set(receipt) != expected_keys
        or receipt.get("schema_version") != 1
        or receipt.get("archive_sha256") != digest
        or receipt.get("release_sha") != active_sha
        or receipt.get("image_reference") != image
        or receipt.get("configuration_sha256") != config_sha
        or not _is_sha(receipt.get("catalogue_sha256"))
        or not _is_sha(receipt.get("artifact_set_sha256"))
        or type(receipt.get("artifact_file_count")) is not int
        or receipt["artifact_file_count"] <= 0
        or not isinstance(result, dict)
        or set(result) != submission._PREPARATION_RESULT_KEYS
        or result.get("operation") != "prepare"
        or result.get("status") != "prepared"
        or result.get("purpose") != contract.PURPOSE
        or result.get("season_number") != contract.SEASON_NUMBER
        or result.get("run_id") != request["run_id"]
        or type(result.get("primary_part_count")) is not int
        or result["primary_part_count"] <= 1
        or receipt["artifact_file_count"] != 3 + result["primary_part_count"]
        or type(result.get("candidate_count")) is not int
        or result["candidate_count"] <= 0
        or type(result.get("file_count")) is not int
        or result["file_count"] != receipt["artifact_file_count"]
        or type(result.get("total_bytes")) is not int
        or result["total_bytes"] <= 0
        or result["total_bytes"] > RUN_ARTIFACT_TOTAL_MAX_BYTES
    ):
        raise NextPrimaryProcessingError("preparation binding invalid")
    estimated = _cost_microusd(result["estimated_primary_cost_usd"])
    if estimated > int(request["maximum_authorized_cost_microusd"]):
        raise NextPrimaryProcessingError("preparation cost exceeds authorization")
    return {
        "receipt": receipt,
        "result": result,
        "receipt_sha": receipt_sha,
        "estimated": estimated,
        "release_sha": active_sha,
        "image": image,
        "config_sha": config_sha,
    }, receipt_sha


def _run_directory(request: Mapping[str, object]) -> Path:
    _directory(REVIEW_RUNS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    root = REVIEW_RUNS_ROOT / f"sha256-{request['archive_sha256']}"
    runs = root / "review-runs"
    _directory(root, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    _directory(runs, mode=0o700, owner=(WORKER_UID, WORKER_GID))
    path = runs / str(request["run_id"])
    try:
        if path.resolve(strict=False).parent != runs.resolve(strict=True):
            raise OSError
    except OSError as error:
        raise NextPrimaryProcessingError("next-primary run invalid") from error
    _directory(path, mode=0o700, owner=(WORKER_UID, WORKER_GID))
    return path


def _base_names(part_count: int) -> set[str]:
    return {
        _CANDIDATES,
        _MANIFEST,
        _STATE,
        *(_REQUEST.format(part=p) for p in range(1, part_count + 1)),
    }


def _inventory(run: Path, part_count: int) -> tuple[dict[str, bytes], dict[str, object]]:
    names = {entry.name for entry in run.iterdir()}
    allowed = (
        _base_names(part_count)
        | {_INTENT.format(part=p) for p in (1, 2)}
        | {_COMPLETED.format(part=p) for p in (1, 2)}
        | {
            "primary-part-0001-output.jsonl",
            "primary-part-0001-api-errors.jsonl",
        }
    )
    if not _base_names(part_count) <= names or not names <= allowed:
        raise NextPrimaryProcessingError("next-primary inventory invalid")
    contents: dict[str, bytes] = {}
    total = 0
    for name in sorted(names):
        raw = _stable(
            run / name,
            maximum=RUN_STATE_MAX_BYTES if name == _STATE else RUN_ARTIFACT_MAX_BYTES,
            mode=0o600,
            owner=(WORKER_UID, WORKER_GID),
        )
        total += len(raw)
        if total > RUN_ARTIFACT_TOTAL_MAX_BYTES:
            raise NextPrimaryProcessingError("next-primary artifacts too large")
        contents[name] = raw
    state = _decode(contents[_STATE], maximum=RUN_STATE_MAX_BYTES)
    if set(state) != observation._RUN_STATE_KEYS or state.get("run_id") is None:
        raise NextPrimaryProcessingError("next-primary run state invalid")
    return contents, state


def _validate_state_shape(state: Mapping[str, object], preparation: Mapping[str, object]) -> None:
    """Validate the complete persisted state before trusting any IDs."""

    result = preparation["result"]
    if (
        set(state) != observation._RUN_STATE_KEYS
        or state.get("schema_version") != 5
        or state.get("run_id") != result.get("run_id")
        or state.get("candidate_count") != result.get("candidate_count")
        or state.get("primary_part_count") != result.get("primary_part_count")
        or state.get("status") not in {"primary_part_completed", "primary_submitted"}
        or not _safe_text(state.get("created_at"), maximum=128)
        or not _safe_text(state.get("updated_at"), maximum=128)
        or not _safe_text(state.get("primary_model"), maximum=128)
        or not _safe_text(state.get("adjudication_model"), maximum=128)
        or not _safe_text(state.get("prompt_version"), maximum=128)
        or not isinstance(state.get("final_review_model"), str)
        or len(state["final_review_model"]) > 128
        or any(ord(character) < 32 for character in state["final_review_model"])
    ):
        raise NextPrimaryProcessingError("next-primary run state invalid")
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
        if type(state.get(field)) is not int or state[field] < 0:
            raise NextPrimaryProcessingError("next-primary run state invalid")
    if (
        state["primary_completed_part_count"] != 1
        or state["primary_part_count"] <= 1
        or any(
            state[field] != 0
            for field in (
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
            )
        )
    ):
        raise NextPrimaryProcessingError("next-primary run state invalid")
    for field in (
        "primary_batch_ids",
        "primary_input_file_ids",
        "adjudication_batch_ids",
        "adjudication_input_file_ids",
        "final_review_batch_ids",
        "final_review_input_file_ids",
    ):
        values = state.get(field)
        if (
            not isinstance(values, list)
            or any(not _safe_text(item) for item in values)
            or len(set(values)) != len(values)
        ):
            raise NextPrimaryProcessingError("next-primary run state invalid")
    completed = state["primary_completed_part_count"]
    expected_id_count = completed if state["status"] == "primary_part_completed" else completed + 1
    if (
        len(state["primary_batch_ids"]) != expected_id_count
        or len(state["primary_input_file_ids"]) != expected_id_count
        or state["adjudication_batch_ids"]
        or state["adjudication_input_file_ids"]
        or state["final_review_batch_ids"]
        or state["final_review_input_file_ids"]
    ):
        raise NextPrimaryProcessingError("next-primary run state invalid")
    for field in (
        "primary_batch_id",
        "primary_input_file_id",
        "adjudication_batch_id",
        "adjudication_input_file_id",
        "final_review_batch_id",
        "final_review_input_file_id",
    ):
        if state[field] is not None and not _safe_text(state[field]):
            raise NextPrimaryProcessingError("next-primary run state invalid")
    if (
        state["primary_batch_id"] != state["primary_batch_ids"][-1]
        or state["primary_input_file_id"] != state["primary_input_file_ids"][-1]
        or any(
            state[field] is not None
            for field in (
                "adjudication_batch_id",
                "adjudication_input_file_id",
                "final_review_batch_id",
                "final_review_input_file_id",
            )
        )
    ):
        raise NextPrimaryProcessingError("next-primary run state invalid")
    for field in (
        "maximum_cost_usd",
        "estimated_primary_cost_usd",
        "actual_primary_cost_usd",
        "actual_adjudication_cost_usd",
        "actual_final_review_cost_usd",
        "actual_total_cost_usd",
    ):
        value = state.get(field)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or value < 0
        ):
            raise NextPrimaryProcessingError("next-primary run state invalid")
    if (
        _cost_microusd(state["estimated_primary_cost_usd"]) != preparation["estimated"]
        or float(state["actual_adjudication_cost_usd"]) != 0
        or float(state["actual_final_review_cost_usd"]) != 0
    ):
        raise NextPrimaryProcessingError("next-primary run state invalid")
    actual_total = sum(
        float(state[field])
        for field in (
            "actual_primary_cost_usd",
            "actual_adjudication_cost_usd",
            "actual_final_review_cost_usd",
        )
    )
    if not math.isclose(
        float(state["actual_total_cost_usd"]),
        actual_total,
        rel_tol=0,
        abs_tol=1e-9,
    ):
        raise NextPrimaryProcessingError("next-primary run state invalid")


def _validate_state_transition(before: Mapping[str, object], after: Mapping[str, object]) -> None:
    mutable = {
        "status",
        "updated_at",
        "primary_batch_id",
        "primary_input_file_id",
        "primary_batch_ids",
        "primary_input_file_ids",
    }
    if (
        before.get("status") != "primary_part_completed"
        or after.get("status") != "primary_submitted"
    ):
        raise NextPrimaryProcessingError("next-primary post-state invalid")
    if any(before[key] != after[key] for key in before.keys() - mutable):
        raise NextPrimaryProcessingError("next-primary post-state invalid")
    before_batch_ids = before.get("primary_batch_ids")
    before_input_ids = before.get("primary_input_file_ids")
    after_batch_ids = after.get("primary_batch_ids")
    after_input_ids = after.get("primary_input_file_ids")
    if (
        not isinstance(before_batch_ids, list)
        or not isinstance(before_input_ids, list)
        or not isinstance(after_batch_ids, list)
        or not isinstance(after_input_ids, list)
        or after_batch_ids[:-1] != before_batch_ids
        or after_input_ids[:-1] != before_input_ids
        or after.get("primary_batch_id") != after_batch_ids[-1]
        or after.get("primary_input_file_id") != after_input_ids[-1]
    ):
        raise NextPrimaryProcessingError("next-primary post-state invalid")


def _set_digest(contents: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(contents):
        encoded = name.encode("ascii")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(contents[name]).to_bytes(8, "big"))
        digest.update(contents[name])
    return digest.hexdigest()


def _validate_journal_pair(
    contents: Mapping[str, bytes],
    *,
    part: int,
    request_sha: str,
    state: Mapping[str, object],
    completed: bool,
) -> None:
    intent_name = _INTENT.format(part=part)
    completed_name = _COMPLETED.format(part=part)
    if intent_name not in contents or completed_name not in contents:
        raise NextPrimaryProcessingError("next-primary journal inventory invalid")
    intent = _decode(contents[intent_name])
    done = _decode(contents[completed_name])
    binding = intent.get("binding")
    if (
        set(intent) != {"binding", "status"}
        or intent.get("status") != "intent"
        or set(done) != {"binding", "batch_id", "input_file_id", "status"}
        or done.get("binding") != binding
        or not isinstance(binding, dict)
        or set(binding)
        != {
            "schema_version",
            "request_sha256",
            "run_id",
            "stage",
            "part",
            "prompt_version",
            "batch_endpoint",
            "completion_window",
        }
        or binding.get("schema_version") != 1
        or binding.get("request_sha256") != request_sha
        or binding.get("run_id") != state["run_id"]
        or binding.get("stage") != "primary"
        or binding.get("part") != part
        or binding.get("prompt_version") != state["prompt_version"]
        or not _safe_text(binding.get("batch_endpoint"), maximum=128)
        or not _safe_text(binding.get("completion_window"), maximum=64)
        or not _safe_text(done.get("batch_id"))
        or not _safe_text(done.get("input_file_id"))
        or not _safe_text(done.get("status"), maximum=128)
    ):
        raise NextPrimaryProcessingError("next-primary journal binding invalid")
    if completed and (
        state["primary_batch_ids"][part - 1] != done["batch_id"]
        or state["primary_input_file_ids"][part - 1] != done["input_file_id"]
    ):
        raise NextPrimaryProcessingError("next-primary journal state binding invalid")


def _validate_journals(
    contents: Mapping[str, bytes],
    state: Mapping[str, object],
) -> str:
    part1_request_sha = _sha(contents[_REQUEST.format(part=1)])
    _validate_journal_pair(
        contents, part=1, request_sha=part1_request_sha, state=state, completed=True
    )
    part2_intent = _INTENT.format(part=2) in contents
    part2_completed = _COMPLETED.format(part=2) in contents
    if state["status"] == "primary_part_completed":
        if part2_completed and not part2_intent:
            raise NextPrimaryProcessingError("next-primary orphan completed journal")
        if part2_completed:
            _validate_journal_pair(
                contents,
                part=2,
                request_sha=_sha(contents[_REQUEST.format(part=2)]),
                state=state,
                completed=False,
            )
            return "completed_pending_state"
        if part2_intent:
            return "intent"
        return "none"
    elif state["status"] == "primary_submitted":
        if not part2_intent or not part2_completed:
            raise NextPrimaryProcessingError("next-primary journal incomplete")
        _validate_journal_pair(
            contents,
            part=2,
            request_sha=_sha(contents[_REQUEST.format(part=2)]),
            state=state,
            completed=True,
        )
        return "completed"
    else:
        raise NextPrimaryProcessingError("next-primary checkpoint invalid")


def _validate_prior_evidence(
    request: Mapping[str, object],
    prep: Mapping[str, object],
    contents: Mapping[str, bytes],
    state: Mapping[str, object],
) -> tuple[str, str, str, str, str, str]:
    """Validate the complete Phase 61/63 root evidence chain."""

    output = "primary-part-0001-output.jsonl"
    if output not in contents or not contents[output]:
        raise NextPrimaryProcessingError("next-primary observation missing")
    base_names = _base_names(int(prep["result"]["primary_part_count"]))
    part_one_journal_names = {
        _INTENT.format(part=1),
        _COMPLETED.format(part=1),
    }
    part_one_journals = {name: contents[name] for name in part_one_journal_names}
    immutable_base_hashes = {name: _sha(contents[name]) for name in base_names if name != _STATE}

    _directory(SUBMISSION_RECEIPTS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    sub_intent, _ = _read_record(SUBMISSION_RECEIPTS_ROOT / f"{request['run_id']}.intent.json")
    sub, sub_sha = _read_record(SUBMISSION_RECEIPTS_ROOT / f"{request['run_id']}.json")
    if (
        set(sub_intent) != submission._ROOT_INTENT_KEYS
        or sub_intent.get("status") != "intent"
        or set(sub) != submission._ROOT_RECEIPT_KEYS
        or any(sub.get(key) != expected for key, expected in sub_intent.items() if key != "status")
    ):
        raise NextPrimaryProcessingError("first submission receipt invalid")
    submit_auth_id = sub.get("authorization_id")
    if not isinstance(submit_auth_id, str):
        raise NextPrimaryProcessingError("first submission authorization invalid")
    submit_auth_raw = _stable(
        AUTHORIZATION_ROOT / f"{submit_auth_id}.json",
        maximum=submission.contract.REQUEST_MAX_BYTES,
        mode=0o600,
        owner=(ROOT_UID, ROOT_GID),
    )
    try:
        submit_auth = submission.contract.parse_request(submit_auth_raw)
        submission_result = submission.contract.validate_aggregate(sub["result"])
    except (TypeError, ValueError) as error:
        raise NextPrimaryProcessingError("first submission receipt invalid") from error
    expected_submit_auth = {
        "archive_sha256": request["archive_sha256"],
        "authorization_id": submit_auth_id,
        "maximum_authorized_cost_microusd": request["maximum_authorized_cost_microusd"],
        "operation": submission.contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": request["run_id"],
        "schema_version": submission.contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
    }
    prepared_hashes = sub.get("prepared_artifact_hashes")
    if (
        submit_auth != expected_submit_auth
        or sub.get("schema_version") != 1
        or sub.get("operation") != submission.contract.OPERATION
        or sub.get("purpose") != contract.PURPOSE
        or sub.get("season_number") != contract.SEASON_NUMBER
        or sub.get("run_id") != request["run_id"]
        or sub.get("archive_sha256") != request["archive_sha256"]
        or sub.get("status") not in {"submitted", "already_submitted"}
        or sub.get("prep_receipt_sha256") != prep["receipt_sha"]
        or sub.get("authorization_sha256") != _sha(submit_auth_raw)
        or sub.get("maximum_authorized_cost_microusd")
        != request["maximum_authorized_cost_microusd"]
        or sub.get("estimated_primary_cost_microusd") != prep["estimated"]
        or sub.get("prepared_artifact_file_count") != prep["receipt"].get("artifact_file_count")
        or sub.get("prepared_artifact_set_sha256") != prep["receipt"].get("artifact_set_sha256")
        or sub.get("primary_part_count") != prep["result"]["primary_part_count"]
        or not isinstance(prepared_hashes, dict)
        or set(prepared_hashes) != base_names
        or any(not _is_sha(value) for value in prepared_hashes.values())
        or any(prepared_hashes[name] != digest for name, digest in immutable_base_hashes.items())
        or sub.get("post_artifact_file_count") != len(base_names) + 2
        or not _is_sha(sub.get("post_artifact_set_sha256"))
        or sub.get("post_journal_file_count") != 2
        or sub.get("post_journal_set_sha256") != _set_digest(part_one_journals)
        or not _is_sha(sub.get("post_run_state_sha256"))
        or submission_result.get("run_id") != request["run_id"]
        or submission_result.get("primary_part_count") != prep["result"]["primary_part_count"]
        or submission_result.get("submitted_part_count") != 1
        or submission_result.get("estimated_primary_cost_microusd") != prep["estimated"]
        or submission_result.get("status") != sub.get("status")
    ):
        raise NextPrimaryProcessingError("first submission binding invalid")

    _directory(OBSERVATION_RECEIPTS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    obs_intent, _ = _read_record(OBSERVATION_RECEIPTS_ROOT / f"{request['run_id']}.intent.json")
    obs, obs_sha = _read_record(OBSERVATION_RECEIPTS_ROOT / f"{request['run_id']}.json")
    result = obs.get("result")
    if (
        set(obs_intent) != observation._INTENT_KEYS
        or obs_intent.get("status") != "intent"
        or set(obs) != observation._RECEIPT_KEYS
        or any(obs.get(key) != expected for key, expected in obs_intent.items() if key != "status")
    ):
        raise NextPrimaryProcessingError("primary observation receipt invalid")
    observation_auth_id = obs.get("authorization_id")
    if not isinstance(observation_auth_id, str):
        raise NextPrimaryProcessingError("primary observation authorization invalid")
    observation_auth_raw = _stable(
        AUTHORIZATION_ROOT / f"{observation_auth_id}.json",
        maximum=observation.contract.REQUEST_MAX_BYTES,
        mode=0o600,
        owner=(ROOT_UID, ROOT_GID),
    )
    try:
        observation_auth = observation.contract.parse_request(observation_auth_raw)
        observed_result = observation.contract.validate_aggregate(result)
    except (TypeError, ValueError) as error:
        raise NextPrimaryProcessingError("primary observation receipt invalid") from error
    expected_observation_auth = {
        "archive_sha256": request["archive_sha256"],
        "authorization_id": observation_auth_id,
        "maximum_authorized_cost_microusd": request["maximum_authorized_cost_microusd"],
        "operation": observation.contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": request["run_id"],
        "schema_version": observation.contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
    }
    observation_hashes = obs.get("prepared_artifact_hashes")
    phase63_contents = {
        name: raw
        for name, raw in contents.items()
        if name not in {_INTENT.format(part=2), _COMPLETED.format(part=2)}
    }
    observation_names = obs.get("pre_observation_observation_names")
    pre_evidence = {
        name: raw
        for name, raw in phase63_contents.items()
        if name != _STATE and name != output and name != "primary-part-0001-api-errors.jsonl"
    }
    if (
        obs.get("schema_version") != 1
        or obs.get("status") != "observed"
        or obs.get("operation") != observation.contract.OPERATION
        or obs.get("purpose") != contract.PURPOSE
        or obs.get("season_number") != contract.SEASON_NUMBER
        or obs.get("archive_sha256") != request["archive_sha256"]
        or obs.get("run_id") != request["run_id"]
        or not isinstance(result, dict)
        or result.get("status") != "observed"
        or result.get("primary_completed_part_count") != 1
        or result.get("primary_part_count") != prep["result"]["primary_part_count"]
        or observation_auth != expected_observation_auth
        or obs.get("authorization_sha256") != _sha(observation_auth_raw)
        or obs.get("maximum_authorized_cost_microusd")
        != request["maximum_authorized_cost_microusd"]
        or obs.get("estimated_primary_cost_microusd") != prep["estimated"]
        or obs.get("prep_receipt_sha256") != prep["receipt_sha"]
        or obs.get("submission_receipt_sha256") != sub_sha
        or obs.get("prepared_artifact_file_count") != prep["receipt"].get("artifact_file_count")
        or obs.get("prepared_artifact_set_sha256") != prep["receipt"].get("artifact_set_sha256")
        or obs.get("primary_part_count") != prep["result"]["primary_part_count"]
        or obs.get("primary_part_number") != 1
        or obs.get("pre_observation_primary_completed_part_count") != 0
        or observation_names != []
        or not isinstance(observation_hashes, dict)
        or set(observation_hashes) != base_names
        or any(not _is_sha(value) for value in observation_hashes.values())
        or observation_hashes.get(_STATE) != sub.get("post_run_state_sha256")
        or any(observation_hashes[name] != digest for name, digest in immutable_base_hashes.items())
        or obs.get("pre_observation_evidence_file_count") != len(pre_evidence)
        or obs.get("pre_observation_evidence_set_sha256") != _set_digest(pre_evidence)
        or observed_result.get("status") != "observed"
        or observed_result.get("run_id") != request["run_id"]
        or observed_result.get("run_status") != "primary_part_completed"
        or observed_result.get("estimated_primary_cost_microusd") != prep["estimated"]
        or not _is_sha(obs.get("pre_observation_state_binding_sha256"))
        or obs.get("pre_observation_run_state_sha256") != sub.get("post_run_state_sha256")
        or obs.get("post_artifact_file_count") != len(phase63_contents)
        or not _is_sha(obs.get("post_artifact_set_sha256"))
        or obs.get("post_journal_file_count") != 2
        or obs.get("post_journal_set_sha256") != _set_digest(part_one_journals)
        or not _is_sha(obs.get("post_run_state_sha256"))
    ):
        raise NextPrimaryProcessingError("primary observation binding invalid")
    if state["status"] == "primary_part_completed" and (
        obs["post_run_state_sha256"] != _sha(contents[_STATE])
        or obs["post_artifact_set_sha256"] != _set_digest(phase63_contents)
    ):
        raise NextPrimaryProcessingError("primary observation evidence changed")
    return (
        sub_sha,
        obs_sha,
        _sha(contents[_REQUEST.format(part=2)]),
        str(obs["post_artifact_set_sha256"]),
        str(obs["post_journal_set_sha256"]),
        str(obs["post_run_state_sha256"]),
    )


def _root_path(run_id: str, suffix: str) -> Path:
    return NEXT_RECEIPTS_ROOT / f"{run_id}{suffix}"


def _fsync_dir(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_once(path: Path, payload: Mapping[str, object]) -> None:
    _directory(NEXT_RECEIPTS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    encoded = _canonical(payload)
    if len(encoded) > ROOT_RECORD_MAX_BYTES:
        raise NextPrimaryProcessingError("next-primary receipt too large")
    staging = path.with_name(f".{path.name}.pending")
    if os.path.lexists(staging):
        try:
            pending = _stable(
                staging,
                maximum=ROOT_RECORD_MAX_BYTES,
                mode=0o600,
                owner=(ROOT_UID, ROOT_GID),
            )
        except NextPrimaryProcessingError:
            staging.unlink(missing_ok=True)
        else:
            if pending != encoded:
                raise NextPrimaryProcessingError("next-primary receipt conflict")
            if os.path.lexists(path):
                existing, _ = _read_record(path)
                if _canonical(existing) != encoded:
                    raise NextPrimaryProcessingError("next-primary receipt conflict")
                staging.unlink()
                _fsync_dir(NEXT_RECEIPTS_ROOT)
                return
            try:
                os.link(staging, path, follow_symlinks=False)
                _fsync_dir(NEXT_RECEIPTS_ROOT)
                staging.unlink()
                _fsync_dir(NEXT_RECEIPTS_ROOT)
                return
            except FileExistsError:
                existing, _ = _read_record(path)
                if _canonical(existing) != encoded:
                    raise NextPrimaryProcessingError("next-primary receipt conflict")
                staging.unlink(missing_ok=True)
                return
    if os.path.lexists(path):
        existing, _ = _read_record(path)
        if _canonical(existing) != encoded:
            raise NextPrimaryProcessingError("next-primary receipt conflict")
        return
    descriptor = -1
    try:
        descriptor = os.open(
            staging, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_dir(NEXT_RECEIPTS_ROOT)
        os.link(staging, path, follow_symlinks=False)
        _fsync_dir(NEXT_RECEIPTS_ROOT)
        os.unlink(staging)
        _fsync_dir(NEXT_RECEIPTS_ROOT)
    except FileExistsError:
        if not os.path.exists(path):
            raise NextPrimaryProcessingError("next-primary receipt conflict") from None
        existing, _ = _read_record(path)
        if _canonical(existing) != encoded:
            raise NextPrimaryProcessingError("next-primary receipt conflict")
    except OSError as error:
        raise NextPrimaryProcessingError("next-primary receipt unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _safe_env() -> dict[str, str]:
    return {
        "PATH": "/usr/sbin:/usr/bin",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _worker_args(request: Mapping[str, object], runs: Path) -> list[str]:
    return [
        "docker",
        "compose",
        "--progress",
        "quiet",
        "--env-file",
        os.fspath(DEV_ENV_FILE),
        "--profile",
        host.REVIEW_NEXT_PRIMARY_COMPOSE_PROFILE,
        "-f",
        os.fspath(COMPOSE_PATH),
        "run",
        "--name",
        host.REVIEW_NEXT_PRIMARY_CONTAINER_NAME,
        "--rm",
        "--no-TTY",
        "--no-deps",
        "--pull",
        "never",
        "--user",
        f"{WORKER_UID}:{WORKER_GID}",
        "--volume",
        f"{runs.as_posix()}:{WORKER_MOUNT}:rw",
        "--env",
        f"{contract.ENV_ARCHIVE_SHA256}={request['archive_sha256']}",
        "--env",
        f"{contract.ENV_RUN_ID}={request['run_id']}",
        "--env",
        f"{contract.ENV_AUTHORIZATION_ID}={request['authorization_id']}",
        "--env",
        f"{contract.ENV_EXPECTED_REQUEST_SHA256}={request[_EXPECTED_REQUEST_KEY]}",
        "--env",
        f"{contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256}={request[_EXPECTED_PRE_ARTIFACTS_KEY]}",
        "--env",
        f"{contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256}={request[_EXPECTED_PRE_JOURNALS_KEY]}",
        "--env",
        f"{contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256}={request[_EXPECTED_PRE_STATE_KEY]}",
        "--env",
        f"{contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD}={request['maximum_authorized_cost_microusd']}",
        host.REVIEW_NEXT_PRIMARY_COMPOSE_SERVICE,
    ]


def _container_identity_is_exact(runs: Path) -> bool:
    try:
        expected = submission._release_image_reference()
        inspected = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .}}",
                host.REVIEW_NEXT_PRIMARY_CONTAINER_NAME,
            ],
            cwd=RELEASE_ROOT,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            check=False,
            timeout=host.REVIEW_NEXT_PRIMARY_KILL_AFTER_SECONDS,
        )
        if inspected.returncode != 0 or len(inspected.stdout) > ROOT_RECORD_MAX_BYTES:
            return False
        value = json.loads(
            inspected.stdout.decode("utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (
        OSError,
        subprocess.SubprocessError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
    ):
        return False
    if not isinstance(value, dict):
        return False
    config = value.get("Config")
    host_config = value.get("HostConfig")
    network_settings = value.get("NetworkSettings")
    mounts = value.get("Mounts")
    if not all(
        isinstance(item, dict) for item in (config, host_config, network_settings)
    ) or not isinstance(mounts, list):
        return False
    labels = config.get("Labels")
    environment = config.get("Env")
    networks = network_settings.get("Networks")
    if (
        value.get("Name") != f"/{host.REVIEW_NEXT_PRIMARY_CONTAINER_NAME}"
        or config.get("Image") != expected
        or config.get("User") != f"{WORKER_UID}:{WORKER_GID}"
        or config.get("WorkingDir") != host.REVIEW_NEXT_PRIMARY_CONTAINER_WORKDIR
        or config.get("Cmd") != list(host.REVIEW_NEXT_PRIMARY_CONTAINER_COMMAND)
        or not isinstance(labels, dict)
        or labels.get("com.docker.compose.service") != host.REVIEW_NEXT_PRIMARY_COMPOSE_SERVICE
        or labels.get("com.docker.compose.oneoff") != "True"
        or labels.get("com.docker.compose.project") != host.REVIEW_NEXT_PRIMARY_COMPOSE_PROJECT
        or labels.get("com.docker.compose.project.config_files") != os.fspath(COMPOSE_PATH)
        or labels.get("com.docker.compose.project.working_dir") != os.fspath(RELEASE_ROOT)
        or not isinstance(environment, list)
        or any(isinstance(item, str) and item.startswith("OPENAI_API_KEY=") for item in environment)
        or host_config.get("ReadonlyRootfs") is not True
        or host_config.get("Privileged") is not False
        or host_config.get("CapDrop") != ["ALL"]
        or "no-new-privileges:true" not in (host_config.get("SecurityOpt") or [])
        or host_config.get("PidsLimit") != 128
        or not isinstance(networks, dict)
        or set(networks) != {host.REVIEW_NEXT_PRIMARY_NETWORK}
    ):
        return False
    destinations: dict[str, tuple[str, bool]] = {}
    for item in mounts:
        if not isinstance(item, dict):
            return False
        source, destination, writable = (
            item.get("Source"),
            item.get("Destination"),
            item.get("RW"),
        )
        if (
            not isinstance(source, str)
            or not isinstance(destination, str)
            or type(writable) is not bool
            or destination in destinations
        ):
            return False
        destinations[destination] = (source, writable)
    return (
        destinations.get(WORKER_MOUNT) == (runs.as_posix(), True)
        and destinations.get(host.REVIEW_NEXT_PRIMARY_SECRET_TARGET, ("", True))[0] != ""
        and destinations.get(host.REVIEW_NEXT_PRIMARY_SECRET_TARGET, ("", True))[1] is False
        and destinations.get(host.REVIEW_NEXT_PRIMARY_TMP_TARGET) == ("", True)
        and set(destinations)
        == {
            WORKER_MOUNT,
            host.REVIEW_NEXT_PRIMARY_SECRET_TARGET,
            host.REVIEW_NEXT_PRIMARY_TMP_TARGET,
        }
    )


def _cleanup_worker(runs: Path) -> None:
    if not _container_identity_is_exact(runs):
        return
    try:
        subprocess.run(
            ["docker", "rm", "--force", host.REVIEW_NEXT_PRIMARY_CONTAINER_NAME],
            cwd=RELEASE_ROOT,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            check=False,
            timeout=host.REVIEW_NEXT_PRIMARY_KILL_AFTER_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return


def _terminate_worker(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            process.wait(timeout=host.REVIEW_NEXT_PRIMARY_KILL_AFTER_SECONDS)
            return
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
    else:
        process.kill()
    process.wait()


def _run_worker(request: Mapping[str, object], runs: Path) -> dict[str, object]:
    process: subprocess.Popen[bytes] | None = None
    try:
        _cleanup_worker(runs)
        process = subprocess.Popen(
            _worker_args(request, runs),
            cwd=RELEASE_ROOT,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=True,
        )
        if process.stdout is None or process.stderr is None:
            raise OSError
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            out = pool.submit(process.stdout.read, contract.OUTPUT_MAX_BYTES + 1)
            err = pool.submit(process.stderr.read, contract.OUTPUT_MAX_BYTES + 1)
            code = process.wait(timeout=host.REVIEW_NEXT_PRIMARY_TIMEOUT_SECONDS - 60)
            stdout, stderr = out.result(timeout=5), err.result(timeout=5)
        if code or stderr or len(stdout) > contract.OUTPUT_MAX_BYTES:
            raise OSError
        return contract.parse_aggregate(stdout)
    except (
        OSError,
        subprocess.SubprocessError,
        TimeoutError,
        TypeError,
        ValueError,
    ) as error:
        raise NextPrimaryProcessingError("next-primary worker failed") from error
    finally:
        if process is not None and process.poll() is None:
            try:
                _terminate_worker(process)
            except (OSError, subprocess.SubprocessError):
                pass
        _cleanup_worker(runs)


def _aggregate(
    request: Mapping[str, object],
    preparation: Mapping[str, object],
    *,
    status: str,
    submitted_part_count: int,
) -> dict[str, object]:
    try:
        return contract.validate_aggregate(
            {
                "estimated_primary_cost_microusd": preparation["estimated"],
                "operation": contract.OPERATION,
                "primary_completed_part_count": 1,
                "primary_part_count": preparation["result"]["primary_part_count"],
                "purpose": contract.PURPOSE,
                "run_id": request["run_id"],
                "season_number": contract.SEASON_NUMBER,
                "status": status,
                "submitted_part_count": submitted_part_count,
            },
            status=status,
        )
    except (TypeError, ValueError) as error:
        raise NextPrimaryProcessingError("next-primary aggregate invalid") from error


def _journal_contents(contents: Mapping[str, bytes]) -> dict[str, bytes]:
    return {name: raw for name, raw in contents.items() if name.startswith(".")}


def _immutable_hashes(contents: Mapping[str, bytes]) -> dict[str, str]:
    mutable = {_STATE, _INTENT.format(part=2), _COMPLETED.format(part=2)}
    return {name: _sha(raw) for name, raw in sorted(contents.items()) if name not in mutable}


def process_request(request: Mapping[str, object]) -> dict[str, object]:
    try:
        request = contract.validate_request(request)
    except (TypeError, ValueError) as error:
        raise NextPrimaryProcessingError("invalid next-primary request") from error
    auth_sha = _validate_authorization(request)
    prep, prep_sha = _validate_preparation(request)
    run = _run_directory(request)
    part_count = int(prep["result"]["primary_part_count"])
    contents, state = _inventory(run, part_count)
    _validate_state_shape(state, prep)
    journal_phase = _validate_journals(contents, state)
    (
        sub_sha,
        obs_sha,
        request_sha,
        phase63_artifact_sha,
        phase63_journal_sha,
        phase63_state_sha,
    ) = _validate_prior_evidence(request, prep, contents, state)
    intent_path = _root_path(str(request["run_id"]), ".intent.json")
    receipt_path = _root_path(str(request["run_id"]), ".json")
    active_sha, image, config_sha = _active_binding()
    binding = {
        "archive_sha256": request["archive_sha256"],
        "authorization_id": request["authorization_id"],
        "authorization_sha256": auth_sha,
        "configuration_sha256": config_sha,
        "estimated_primary_cost_microusd": prep["estimated"],
        "first_submission_receipt_sha256": sub_sha,
        "observation_receipt_sha256": obs_sha,
        "operation": contract.OPERATION,
        "prep_receipt_sha256": prep_sha,
        "prepared_artifact_hashes": _immutable_hashes(contents),
        "primary_part_count": part_count,
        "primary_part_number": 2,
        "purpose": contract.PURPOSE,
        "release_sha": active_sha,
        "image_reference": image,
        "run_id": request["run_id"],
        "schema_version": _RECEIPT_SCHEMA,
        "season_number": contract.SEASON_NUMBER,
        "maximum_authorized_cost_microusd": request["maximum_authorized_cost_microusd"],
        "request_sha256": request_sha,
        "pre_artifact_set_sha256": phase63_artifact_sha,
        "pre_journal_set_sha256": phase63_journal_sha,
        "pre_run_state_sha256": phase63_state_sha,
        "status": "intent",
    }

    intent_exists = os.path.lexists(intent_path)
    receipt_exists = os.path.lexists(receipt_path)
    if receipt_exists and not intent_exists:
        raise NextPrimaryProcessingError("orphan next-primary receipt")
    if intent_exists:
        intent, _ = _read_record(intent_path)
        if set(intent) != _BINDING_KEYS or intent != binding:
            raise NextPrimaryProcessingError("next-primary intent binding changed")
    else:
        if receipt_exists or state["status"] != "primary_part_completed" or journal_phase != "none":
            raise NextPrimaryProcessingError("next-primary intent missing")
        _write_once(intent_path, binding)
        intent = binding

    if receipt_exists:
        if state["status"] != "primary_submitted" or journal_phase != "completed":
            raise NextPrimaryProcessingError("next-primary receipt state invalid")
        receipt, _ = _read_record(receipt_path)
        if set(receipt) != _RECEIPT_KEYS or any(
            receipt.get(key) != expected for key, expected in intent.items() if key != "status"
        ):
            raise NextPrimaryProcessingError("next-primary receipt binding changed")
        try:
            stored_result = contract.validate_aggregate(receipt.get("result"))
        except (TypeError, ValueError) as error:
            raise NextPrimaryProcessingError("next-primary receipt invalid") from error
        if (
            receipt.get("status") not in {"submitted", "already_submitted"}
            or stored_result.get("status") != receipt.get("status")
            or stored_result.get("run_id") != request["run_id"]
            or stored_result.get("primary_part_count") != part_count
            or stored_result.get("primary_completed_part_count") != 1
            or stored_result.get("submitted_part_count") != 1
            or stored_result.get("estimated_primary_cost_microusd") != prep["estimated"]
            or receipt.get("post_artifact_set_sha256") != _set_digest(contents)
            or receipt.get("post_run_state_sha256") != _sha(contents[_STATE])
            or receipt.get("post_journal_set_sha256") != _set_digest(_journal_contents(contents))
            or receipt.get("post_artifact_file_count") != len(contents)
        ):
            raise NextPrimaryProcessingError("next-primary receipt evidence changed")
        return _aggregate(
            request,
            prep,
            status="already_submitted",
            submitted_part_count=1,
        )

    if state["status"] == "primary_submitted":
        if journal_phase != "completed":
            raise NextPrimaryProcessingError("next-primary post-state invalid")
        result = _aggregate(
            request,
            prep,
            status="already_submitted",
            submitted_part_count=1,
        )
    else:
        if _sha(contents[_STATE]) != intent["pre_run_state_sha256"]:
            raise NextPrimaryProcessingError("next-primary pre-state changed")
        if journal_phase == "intent":
            return _aggregate(
                request,
                prep,
                status="reconciliation_required",
                submitted_part_count=0,
            )
        if journal_phase not in {"none", "completed_pending_state"}:
            raise NextPrimaryProcessingError("next-primary journal state invalid")
        before_state = dict(state)
        before_names = set(contents)
        before_hashes = {name: _sha(raw) for name, raw in contents.items() if name != _STATE}
        worker_result = _run_worker(
            {
                **request,
                _EXPECTED_REQUEST_KEY: request_sha,
                _EXPECTED_PRE_ARTIFACTS_KEY: phase63_artifact_sha,
                _EXPECTED_PRE_JOURNALS_KEY: phase63_journal_sha,
                _EXPECTED_PRE_STATE_KEY: phase63_state_sha,
            },
            run.parent,
        )
        try:
            checked_worker = contract.validate_aggregate(worker_result)
        except (TypeError, ValueError) as error:
            raise NextPrimaryProcessingError("next-primary worker result invalid") from error
        if (
            checked_worker["run_id"] != request["run_id"]
            or checked_worker["primary_part_count"] != part_count
            or checked_worker["primary_completed_part_count"] != 1
            or checked_worker["estimated_primary_cost_microusd"] != prep["estimated"]
        ):
            raise NextPrimaryProcessingError("next-primary worker result invalid")
        contents, state = _inventory(run, part_count)
        _validate_state_shape(state, prep)
        journal_phase = _validate_journals(contents, state)
        if checked_worker["status"] == "reconciliation_required":
            if (
                checked_worker["submitted_part_count"] != 0
                or state["status"] != "primary_part_completed"
                or journal_phase != "intent"
                or set(contents) - before_names != {_INTENT.format(part=2)}
            ):
                raise NextPrimaryProcessingError("next-primary reconciliation invalid")
            return _aggregate(
                request,
                prep,
                status="reconciliation_required",
                submitted_part_count=0,
            )
        if (
            checked_worker["status"] not in {"submitted", "already_submitted"}
            or checked_worker["submitted_part_count"] != 1
            or state["status"] != "primary_submitted"
            or journal_phase != "completed"
            or _sha(contents[_REQUEST.format(part=2)]) != request_sha
        ):
            raise NextPrimaryProcessingError("next-primary worker result invalid")
        expected_additions = (
            {_INTENT.format(part=2), _COMPLETED.format(part=2)}
            if journal_phase == "completed" and _INTENT.format(part=2) not in before_names
            else set()
        )
        if set(contents) - before_names != expected_additions or any(
            _sha(contents[name]) != digest for name, digest in before_hashes.items()
        ):
            raise NextPrimaryProcessingError("next-primary post-inventory invalid")
        _validate_state_transition(before_state, state)
        result = _aggregate(
            request,
            prep,
            status=(
                "already_submitted"
                if checked_worker["status"] == "already_submitted"
                else "submitted"
            ),
            submitted_part_count=1,
        )

    if _active_binding() != (active_sha, image, config_sha):
        raise NextPrimaryProcessingError("active runtime changed")
    receipt = {
        **intent,
        "post_artifact_set_sha256": _set_digest(contents),
        "post_run_state_sha256": _sha(contents[_STATE]),
        "post_journal_set_sha256": _set_digest(_journal_contents(contents)),
        "post_artifact_file_count": len(contents),
        "status": result["status"],
        "result": result,
    }
    _write_once(receipt_path, receipt)
    return result


def _require_root() -> None:
    if (
        os.name != "posix"
        or os.geteuid() != 0
        or os.environ.get("SUDO_USER") != host.REVIEW_USER
        or platform.system() != "Linux"
        or platform.machine() != "x86_64"
    ):
        raise NextPrimaryProcessingError("invalid next-primary caller")


def main() -> int:
    try:
        _require_root()
        result = process_request(_read_request(sys.stdin.buffer))
        sys.stdout.buffer.write(contract.canonical_json(result))
        return 0
    except Exception:
        sys.stderr.write("error=speaker_review_next_primary_rejected\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
