"""Root-only coordinator for observing exactly primary part two.

This boundary is intentionally separate from the original part-one observer.
It consumes the Phase 65 receipt, performs one read-only provider observation,
and publishes a part-specific receipt only after the filesystem transition is
verified.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import platform
import signal
import stat
import subprocess
import sys
from pathlib import Path
from typing import BinaryIO, Final, Mapping

_SCRIPTS = Path(__file__).resolve().parent
if os.fspath(_SCRIPTS) not in sys.path:
    sys.path.insert(0, os.fspath(_SCRIPTS))

try:
    from scripts import private_speaker_review_next_primary_observation_contract as contract
    from scripts import private_speaker_review_next_primary_observation_host_contract as host
    from scripts import private_speaker_review_next_primary_submission_contract as submit_contract
    from scripts import run_private_speaker_review_next_primary as submit
    from scripts import run_private_speaker_review_observation as observation
except ModuleNotFoundError:
    import private_speaker_review_next_primary_observation_contract as contract
    import private_speaker_review_next_primary_observation_host_contract as host
    import private_speaker_review_next_primary_submission_contract as submit_contract
    import run_private_speaker_review_next_primary as submit
    import run_private_speaker_review_observation as observation


class NextPrimaryObservationError(RuntimeError):
    """Generic rejection that never exposes private/provider details."""


RELEASE_ROOT: Final = Path(__file__).resolve().parents[1]
RUNS_ROOT: Final = host.SPEAKER_REVIEW_RUNS_ROOT
AUTHORIZATION_ROOT: Final = host.REVIEW_AUTHORIZATION_ROOT
PREPARATION_ROOT: Final = host.SPEAKER_REVIEW_ROOT / "receipts"
NEXT_RECEIPTS_ROOT: Final = host.SPEAKER_REVIEW_ROOT / "next-primary-receipts"
OBSERVATION_RECEIPTS_ROOT: Final = host.REVIEW_NEXT_OBSERVATION_RECEIPTS_ROOT
COMPOSE_PATH: Final = RELEASE_ROOT / "deploy/compose.yaml"
ENV_FILE: Final = host.ENV_FILE
WORKER_MOUNT: Final = "/review-workspace/review-runs"
WORKER_UID: Final = host.UID_IN_CONTAINER
WORKER_GID: Final = host.GID_IN_CONTAINER
ROOT_UID: Final = 0
ROOT_GID: Final = 0
MAX_RECORD_BYTES: Final = 128 * 1024
MAX_FILE_BYTES: Final = 64 * 1024 * 1024
MAX_TOTAL_BYTES: Final = 256 * 1024 * 1024
STATE = "run-state.json"
REQUEST = "primary-part-{part:04d}-requests.jsonl"
INTENT = ".primary-part-{part:04d}-submission-intent.json"
COMPLETED = ".primary-part-{part:04d}-submission-completed.json"
PART2_OUTPUTS = frozenset({"primary-part-0002-output.jsonl", "primary-part-0002-api-errors.jsonl"})
TERMINAL_OUTPUT = "terminal-api-errors.jsonl"
_MUTABLE_OBSERVATION_STATE_FIELDS = frozenset(
    {"status", "updated_at", "primary_completed_part_count"}
)
_BINDING_KEYS = frozenset(
    {
        "archive_sha256",
        "authorization_id",
        "authorization_sha256",
        "configuration_sha256",
        "estimated_primary_cost_microusd",
        "first_observation_receipt_sha256",
        "first_submission_receipt_sha256",
        "image_reference",
        "maximum_authorized_cost_microusd",
        "next_primary_intent_sha256",
        "next_primary_submission_receipt_sha256",
        "operation",
        "pre_artifact_hashes",
        "pre_artifact_set_sha256",
        "pre_evidence_set_sha256",
        "pre_journal_set_sha256",
        "pre_primary_completed_part_count",
        "pre_run_state_sha256",
        "pre_state_binding_sha256",
        "prep_receipt_sha256",
        "prepared_artifact_set_sha256",
        "primary_part_count",
        "primary_part_number",
        "purpose",
        "release_sha",
        "request_sha256",
        "run_id",
        "schema_version",
        "season_number",
        "status",
    }
)
_RECEIPT_KEYS = frozenset(
    {
        *_BINDING_KEYS,
        "post_artifact_file_count",
        "post_artifact_set_sha256",
        "post_journal_file_count",
        "post_journal_set_sha256",
        "post_run_state_sha256",
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


def _set_digest(contents: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(contents):
        encoded = name.encode("ascii")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(contents[name]).to_bytes(8, "big"))
        digest.update(contents[name])
    return digest.hexdigest()


def _artifact_digest(contents: Mapping[str, bytes]) -> str:
    return _set_digest(
        {name: raw for name, raw in contents.items() if not name.startswith(".primary-part-")}
    )


def _journal_digest(contents: Mapping[str, bytes]) -> str:
    return _set_digest(
        {name: raw for name, raw in contents.items() if name.startswith(".primary-part-")}
    )


def _artifact_hashes(contents: Mapping[str, bytes]) -> dict[str, str]:
    return {name: _sha(raw) for name, raw in sorted(contents.items())}


def _state_binding_sha256(state: Mapping[str, object]) -> str:
    return _sha(
        _canonical(
            {
                key: value
                for key, value in state.items()
                if key not in _MUTABLE_OBSERVATION_STATE_FIELDS
            }
        )
    )


def _directory(path: Path, *, mode: int, owner: tuple[int, int]) -> None:
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != mode
            or (metadata.st_uid, metadata.st_gid) != owner
            or path.resolve(strict=True) != path
        ):
            raise OSError
    except OSError as error:
        raise NextPrimaryObservationError("observation evidence unavailable") from error


def _stable(path: Path, *, maximum: int, mode: int, owner: tuple[int, int]) -> bytes:
    descriptor = -1
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > maximum
            or stat.S_IMODE(before.st_mode) != mode
            or (before.st_uid, before.st_gid) != owner
        ):
            raise OSError
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        raw = os.read(descriptor, maximum + 1)
        after = path.lstat()
    except OSError as error:
        raise NextPrimaryObservationError("observation evidence unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)

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
        raise NextPrimaryObservationError("observation evidence changed")
    return raw


def _decode(raw: bytes) -> dict[str, object]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=submit._unique_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise NextPrimaryObservationError("observation evidence invalid") from error
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise NextPrimaryObservationError("observation evidence invalid")
    return value


def _read_request(stream: BinaryIO) -> dict[str, object]:
    raw = stream.readline(contract.REQUEST_MAX_BYTES + 1)
    if stream.read(1):
        raise NextPrimaryObservationError("invalid observation request")
    try:
        return contract.parse_request(raw)
    except (TypeError, ValueError) as error:
        raise NextPrimaryObservationError("invalid observation request") from error


def _read_record(path: Path) -> tuple[dict[str, object], str]:
    raw = _stable(path, maximum=MAX_RECORD_BYTES, mode=0o600, owner=(ROOT_UID, ROOT_GID))
    return _decode(raw), _sha(raw)


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
        raise NextPrimaryObservationError("observation authorization invalid") from error
    return _sha(raw)


def _run_directory(request: Mapping[str, object]) -> Path:
    _directory(RUNS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    object_root = RUNS_ROOT / f"sha256-{request['archive_sha256']}"
    runs = object_root / "review-runs"
    _directory(object_root, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    _directory(runs, mode=0o700, owner=(WORKER_UID, WORKER_GID))
    path = runs / str(request["run_id"])
    if path.resolve(strict=False).parent != runs.resolve(strict=True):
        raise NextPrimaryObservationError("observation run invalid")
    _directory(path, mode=0o700, owner=(WORKER_UID, WORKER_GID))
    return path


def _inventory(run: Path, part_count: int) -> tuple[dict[str, bytes], dict[str, object]]:
    base = submit._base_names(part_count)
    allowed = base | {
        INTENT.format(part=1),
        COMPLETED.format(part=1),
        INTENT.format(part=2),
        COMPLETED.format(part=2),
        "primary-part-0001-output.jsonl",
        "primary-part-0001-api-errors.jsonl",
        *PART2_OUTPUTS,
        TERMINAL_OUTPUT,
    }
    names = {entry.name for entry in run.iterdir()}
    if not base <= names or not names <= allowed:
        raise NextPrimaryObservationError("observation inventory invalid")
    contents: dict[str, bytes] = {}
    total = 0
    for name in sorted(names):
        raw = _stable(
            run / name,
            maximum=64 * 1024 if name == STATE else MAX_FILE_BYTES,
            mode=0o600,
            owner=(WORKER_UID, WORKER_GID),
        )
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise NextPrimaryObservationError("observation artifacts too large")
        contents[name] = raw
    state = _decode(contents[STATE])
    if set(state) != observation._RUN_STATE_KEYS:
        raise NextPrimaryObservationError("observation run state invalid")
    return contents, state


def _validate_pre_state(state: Mapping[str, object], prep: Mapping[str, object]) -> None:
    try:
        submit._validate_state_shape(state, prep)
        if state["status"] != "primary_submitted" or state["primary_completed_part_count"] != 1:
            raise ValueError
    except (TypeError, ValueError, KeyError, submit.NextPrimaryProcessingError) as error:
        raise NextPrimaryObservationError("observation checkpoint invalid") from error


def _validate_next_receipt(
    request: Mapping[str, object],
    prep: Mapping[str, object],
    contents: Mapping[str, bytes],
    *,
    first_submission_sha256: str,
    first_observation_sha256: str,
    phase63_artifact_sha256: str,
    phase63_journal_sha256: str,
    phase63_state_sha256: str,
) -> tuple[str, str]:
    _directory(NEXT_RECEIPTS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    intent, intent_sha = _read_record(NEXT_RECEIPTS_ROOT / f"{request['run_id']}.intent.json")
    receipt, receipt_sha = _read_record(NEXT_RECEIPTS_ROOT / f"{request['run_id']}.json")
    if (
        set(intent) != submit._BINDING_KEYS
        or intent.get("status") != "intent"
        or set(receipt) != submit._RECEIPT_KEYS
        or any(receipt.get(key) != value for key, value in intent.items() if key != "status")
    ):
        raise NextPrimaryObservationError("next-primary receipt invalid")
    auth_id = receipt.get("authorization_id")
    if not isinstance(auth_id, str):
        raise NextPrimaryObservationError("next-primary authorization invalid")
    auth_raw = _stable(
        AUTHORIZATION_ROOT / f"{auth_id}.json",
        maximum=submit_contract.REQUEST_MAX_BYTES,
        mode=0o600,
        owner=(ROOT_UID, ROOT_GID),
    )
    try:
        authorized = submit_contract.parse_request(auth_raw)
        result = submit_contract.validate_aggregate(receipt.get("result"))
    except (TypeError, ValueError) as error:
        raise NextPrimaryObservationError("next-primary receipt invalid") from error
    expected_auth = {
        "archive_sha256": request["archive_sha256"],
        "authorization_id": auth_id,
        "maximum_authorized_cost_microusd": request["maximum_authorized_cost_microusd"],
        "operation": submit_contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": request["run_id"],
        "schema_version": submit_contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
    }
    if (
        authorized != expected_auth
        or intent.get("schema_version") != 1
        or intent.get("operation") != submit_contract.OPERATION
        or intent.get("purpose") != contract.PURPOSE
        or intent.get("season_number") != contract.SEASON_NUMBER
        or intent.get("archive_sha256") != request["archive_sha256"]
        or intent.get("run_id") != request["run_id"]
        or intent.get("release_sha") != prep["release_sha"]
        or intent.get("image_reference") != prep["image"]
        or intent.get("configuration_sha256") != prep["config_sha"]
        or intent.get("prep_receipt_sha256") != prep["receipt_sha"]
        or intent.get("prepared_artifact_set_sha256") != prep["receipt"].get("artifact_set_sha256")
        or intent.get("first_submission_receipt_sha256") != first_submission_sha256
        or intent.get("observation_receipt_sha256") != first_observation_sha256
        or intent.get("pre_artifact_set_sha256") != phase63_artifact_sha256
        or intent.get("pre_journal_set_sha256") != phase63_journal_sha256
        or intent.get("pre_run_state_sha256") != phase63_state_sha256
        or intent.get("prepared_artifact_hashes") != submit._immutable_hashes(contents)
        or intent.get("estimated_primary_cost_microusd") != prep["estimated"]
        or receipt.get("status") not in {"submitted", "already_submitted"}
        or intent.get("request_sha256") != _sha(contents[REQUEST.format(part=2)])
        or intent.get("primary_part_number") != 2
        or intent.get("primary_part_count") != prep["result"]["primary_part_count"]
        or intent.get("maximum_authorized_cost_microusd")
        != request["maximum_authorized_cost_microusd"]
        or intent.get("authorization_sha256") != _sha(auth_raw)
        or result.get("status") != receipt.get("status")
        or result.get("run_id") != request["run_id"]
        or result.get("primary_part_count") != prep["result"]["primary_part_count"]
        or result.get("primary_completed_part_count") != 1
        or result.get("submitted_part_count") != 1
        or result.get("estimated_primary_cost_microusd") != prep["estimated"]
        or receipt.get("post_artifact_file_count") != len(contents)
        or receipt.get("post_artifact_set_sha256") != _set_digest(contents)
        or receipt.get("post_journal_set_sha256") != _journal_digest(contents)
        or receipt.get("post_run_state_sha256") != _sha(contents[STATE])
    ):
        raise NextPrimaryObservationError("next-primary receipt binding changed")
    return intent_sha, receipt_sha


def _journals(contents: Mapping[str, bytes]) -> dict[str, bytes]:
    return {name: raw for name, raw in contents.items() if name.startswith(".primary-part-")}


def _validate_part2_journals(contents: Mapping[str, bytes], state: Mapping[str, object]) -> None:
    try:
        submit._validate_journal_pair(
            contents,
            part=1,
            request_sha=_sha(contents[REQUEST.format(part=1)]),
            state=state,
            completed=True,
        )
        submit._validate_journal_pair(
            contents,
            part=2,
            request_sha=_sha(contents[REQUEST.format(part=2)]),
            state=state,
            completed=True,
        )
    except (KeyError, TypeError, ValueError, submit.NextPrimaryProcessingError) as error:
        raise NextPrimaryObservationError("observation journal invalid") from error


def _root_binding(
    request: Mapping[str, object],
    prep: Mapping[str, object],
    auth_sha: str,
    contents: Mapping[str, bytes],
    state: Mapping[str, object],
    first_sha: str,
    first_observation_sha: str,
    next_intent_sha: str,
    next_sha: str,
) -> dict[str, object]:
    binding: dict[str, object] = {
        "archive_sha256": request["archive_sha256"],
        "authorization_id": request["authorization_id"],
        "authorization_sha256": auth_sha,
        "estimated_primary_cost_microusd": prep["estimated"],
        "first_submission_receipt_sha256": first_sha,
        "first_observation_receipt_sha256": first_observation_sha,
        "next_primary_intent_sha256": next_intent_sha,
        "next_primary_submission_receipt_sha256": next_sha,
        "operation": contract.OPERATION,
        "prep_receipt_sha256": prep["receipt_sha"],
        "prepared_artifact_set_sha256": prep["receipt"].get("artifact_set_sha256"),
        "primary_part_count": prep["result"]["primary_part_count"],
        "primary_part_number": 2,
        "purpose": contract.PURPOSE,
        "release_sha": prep["release_sha"],
        "image_reference": prep["image"],
        "configuration_sha256": prep["config_sha"],
        "run_id": request["run_id"],
        "schema_version": 1,
        "season_number": contract.SEASON_NUMBER,
        "maximum_authorized_cost_microusd": request["maximum_authorized_cost_microusd"],
        "request_sha256": _sha(contents[REQUEST.format(part=2)]),
        "pre_artifact_hashes": _artifact_hashes(contents),
        "pre_artifact_set_sha256": _artifact_digest(contents),
        "pre_evidence_set_sha256": _set_digest(contents),
        "pre_journal_set_sha256": _journal_digest(contents),
        "pre_run_state_sha256": _sha(contents[STATE]),
        "pre_state_binding_sha256": _state_binding_sha256(state),
        "pre_primary_completed_part_count": state["primary_completed_part_count"],
        "status": "intent",
    }
    if set(binding) != _BINDING_KEYS:
        raise NextPrimaryObservationError("observation intent invalid")
    return binding


def _validate_intent(
    value: object,
    *,
    request: Mapping[str, object],
    prep: Mapping[str, object],
    authorization_sha256: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _BINDING_KEYS:
        raise NextPrimaryObservationError("observation intent invalid")
    expected = {
        "archive_sha256": request["archive_sha256"],
        "authorization_id": request["authorization_id"],
        "authorization_sha256": authorization_sha256,
        "configuration_sha256": prep["config_sha"],
        "estimated_primary_cost_microusd": prep["estimated"],
        "image_reference": prep["image"],
        "maximum_authorized_cost_microusd": request["maximum_authorized_cost_microusd"],
        "operation": contract.OPERATION,
        "pre_primary_completed_part_count": 1,
        "prep_receipt_sha256": prep["receipt_sha"],
        "prepared_artifact_set_sha256": prep["receipt"].get("artifact_set_sha256"),
        "primary_part_count": prep["result"]["primary_part_count"],
        "primary_part_number": 2,
        "purpose": contract.PURPOSE,
        "release_sha": prep["release_sha"],
        "run_id": request["run_id"],
        "schema_version": 1,
        "season_number": contract.SEASON_NUMBER,
        "status": "intent",
    }
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise NextPrimaryObservationError("observation intent binding changed")
    hashes = value.get("pre_artifact_hashes")
    if (
        not isinstance(hashes, dict)
        or STATE not in hashes
        or REQUEST.format(part=2) not in hashes
        or INTENT.format(part=2) not in hashes
        or COMPLETED.format(part=2) not in hashes
        or any(not isinstance(name, str) or not _is_sha(digest) for name, digest in hashes.items())
        or any(
            not _is_sha(value.get(key))
            for key in (
                "first_observation_receipt_sha256",
                "first_submission_receipt_sha256",
                "next_primary_intent_sha256",
                "next_primary_submission_receipt_sha256",
                "pre_artifact_set_sha256",
                "pre_evidence_set_sha256",
                "pre_journal_set_sha256",
                "pre_run_state_sha256",
                "pre_state_binding_sha256",
                "request_sha256",
            )
        )
        or hashes.get(STATE) != value.get("pre_run_state_sha256")
    ):
        raise NextPrimaryObservationError("observation intent invalid")
    return dict(value)


def _validate_predecessors_from_intent(
    intent: Mapping[str, object],
    *,
    request: Mapping[str, object],
) -> None:
    """Revalidate root-owned predecessor records during receipt repair/replay."""

    prep_path = PREPARATION_ROOT / f"sha256-{request['archive_sha256']}.json"
    _, prep_sha = _read_record(prep_path)
    _, first_sha = _read_record(submit.SUBMISSION_RECEIPTS_ROOT / f"{request['run_id']}.json")
    _, first_observation_sha = _read_record(
        submit.OBSERVATION_RECEIPTS_ROOT / f"{request['run_id']}.json"
    )
    next_intent, next_intent_sha = _read_record(
        NEXT_RECEIPTS_ROOT / f"{request['run_id']}.intent.json"
    )
    next_receipt, next_receipt_sha = _read_record(NEXT_RECEIPTS_ROOT / f"{request['run_id']}.json")
    if (
        prep_sha != intent["prep_receipt_sha256"]
        or first_sha != intent["first_submission_receipt_sha256"]
        or first_observation_sha != intent["first_observation_receipt_sha256"]
        or next_intent_sha != intent["next_primary_intent_sha256"]
        or next_receipt_sha != intent["next_primary_submission_receipt_sha256"]
        or set(next_intent) != submit._BINDING_KEYS
        or set(next_receipt) != submit._RECEIPT_KEYS
        or next_intent.get("status") != "intent"
        or any(
            next_receipt.get(key) != item for key, item in next_intent.items() if key != "status"
        )
        or next_intent.get("archive_sha256") != request["archive_sha256"]
        or next_intent.get("run_id") != request["run_id"]
        or next_intent.get("release_sha") != intent["release_sha"]
        or next_intent.get("image_reference") != intent["image_reference"]
        or next_intent.get("configuration_sha256") != intent["configuration_sha256"]
        or next_intent.get("prep_receipt_sha256") != intent["prep_receipt_sha256"]
        or next_intent.get("first_submission_receipt_sha256")
        != intent["first_submission_receipt_sha256"]
        or next_intent.get("observation_receipt_sha256")
        != intent["first_observation_receipt_sha256"]
        or next_intent.get("primary_part_number") != 2
        or next_intent.get("request_sha256") != intent["request_sha256"]
        or next_receipt.get("status") not in {"submitted", "already_submitted"}
        or next_receipt.get("post_artifact_file_count") != len(intent["pre_artifact_hashes"])
        or next_receipt.get("post_artifact_set_sha256") != intent["pre_evidence_set_sha256"]
        or next_receipt.get("post_journal_set_sha256") != intent["pre_journal_set_sha256"]
        or next_receipt.get("post_run_state_sha256") != intent["pre_run_state_sha256"]
    ):
        raise NextPrimaryObservationError("observation predecessor evidence changed")
    next_authorization_id = next_intent.get("authorization_id")
    if not isinstance(next_authorization_id, str):
        raise NextPrimaryObservationError("observation predecessor evidence invalid")
    next_authorization_raw = _stable(
        AUTHORIZATION_ROOT / f"{next_authorization_id}.json",
        maximum=submit_contract.REQUEST_MAX_BYTES,
        mode=0o600,
        owner=(ROOT_UID, ROOT_GID),
    )
    try:
        next_authorization = submit_contract.parse_request(next_authorization_raw)
        next_result = submit_contract.validate_aggregate(next_receipt.get("result"))
    except (TypeError, ValueError) as error:
        raise NextPrimaryObservationError("observation predecessor evidence invalid") from error
    expected_authorization = {
        "archive_sha256": request["archive_sha256"],
        "authorization_id": next_authorization_id,
        "maximum_authorized_cost_microusd": request["maximum_authorized_cost_microusd"],
        "operation": submit_contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": request["run_id"],
        "schema_version": submit_contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
    }
    if (
        next_authorization != expected_authorization
        or next_intent.get("authorization_sha256") != _sha(next_authorization_raw)
        or next_result.get("status") != next_receipt.get("status")
        or next_result.get("run_id") != request["run_id"]
        or next_result.get("primary_completed_part_count") != 1
        or next_result.get("submitted_part_count") != 1
    ):
        raise NextPrimaryObservationError("observation predecessor evidence invalid")


def _validate_checkpoint_against_intent(
    contents: Mapping[str, bytes],
    state: Mapping[str, object],
    intent: Mapping[str, object],
) -> None:
    hashes = intent.get("pre_artifact_hashes")
    if (
        not isinstance(hashes, dict)
        or _artifact_hashes(contents) != hashes
        or _artifact_digest(contents) != intent["pre_artifact_set_sha256"]
        or _set_digest(contents) != intent["pre_evidence_set_sha256"]
        or _journal_digest(contents) != intent["pre_journal_set_sha256"]
        or _sha(contents[STATE]) != intent["pre_run_state_sha256"]
        or _state_binding_sha256(state) != intent["pre_state_binding_sha256"]
    ):
        raise NextPrimaryObservationError("observation checkpoint changed")


def _validate_recovered_transition(
    contents: Mapping[str, bytes],
    state: Mapping[str, object],
    intent: Mapping[str, object],
    *,
    status: str,
) -> None:
    hashes = intent.get("pre_artifact_hashes")
    if not isinstance(hashes, dict):
        raise NextPrimaryObservationError("observation intent invalid")
    expected_names = set(hashes)
    if status == "observed":
        if "primary-part-0002-output.jsonl" not in contents:
            raise NextPrimaryObservationError("observation output missing")
        allowed_additions = PART2_OUTPUTS
        if (
            state.get("status") != "primary_part_completed"
            or state.get("primary_completed_part_count") != 2
        ):
            raise NextPrimaryObservationError("observation post-state invalid")
    elif status == "failed":
        allowed_additions = frozenset({TERMINAL_OUTPUT})
        if state.get("status") != "failed" or state.get("primary_completed_part_count") != 1:
            raise NextPrimaryObservationError("observation post-state invalid")
    else:
        raise NextPrimaryObservationError("observation status invalid")
    additions = set(contents) - expected_names
    if (
        not additions <= allowed_additions
        or not expected_names <= set(contents)
        or any(name != STATE and _sha(contents[name]) != digest for name, digest in hashes.items())
        or _state_binding_sha256(state) != intent["pre_state_binding_sha256"]
        or not submit._safe_text(state.get("updated_at"), maximum=128)
    ):
        raise NextPrimaryObservationError("observation evidence changed")


def _receipt_payload(
    intent: Mapping[str, object],
    result: Mapping[str, object],
    contents: Mapping[str, bytes],
) -> dict[str, object]:
    value = {
        **intent,
        "post_artifact_file_count": len(contents),
        "post_artifact_set_sha256": _set_digest(contents),
        "post_journal_file_count": len(_journals(contents)),
        "post_journal_set_sha256": _journal_digest(contents),
        "post_run_state_sha256": _sha(contents[STATE]),
        "result": dict(result),
        "status": result["status"],
    }
    if set(value) != _RECEIPT_KEYS:
        raise NextPrimaryObservationError("observation receipt invalid")
    return value


def _validate_final_receipt(
    value: object,
    *,
    intent: Mapping[str, object],
    contents: Mapping[str, bytes],
    expected_status: str,
) -> dict[str, object]:
    if (
        not isinstance(value, dict)
        or set(value) != _RECEIPT_KEYS
        or any(value.get(key) != item for key, item in intent.items() if key != "status")
        or value.get("status") != expected_status
        or value.get("post_artifact_file_count") != len(contents)
        or value.get("post_artifact_set_sha256") != _set_digest(contents)
        or value.get("post_journal_file_count") != len(_journals(contents))
        or value.get("post_journal_set_sha256") != _journal_digest(contents)
        or value.get("post_run_state_sha256") != _sha(contents[STATE])
    ):
        raise NextPrimaryObservationError("observation receipt evidence changed")
    try:
        return contract.validate_aggregate(value.get("result"), status=expected_status)
    except (TypeError, ValueError) as error:
        raise NextPrimaryObservationError("observation receipt invalid") from error


def _aggregate(
    request: Mapping[str, object],
    prep: Mapping[str, object],
    state: Mapping[str, object],
    status: str,
) -> dict[str, object]:
    value = {
        "estimated_primary_cost_microusd": prep["estimated"],
        "operation": contract.OPERATION,
        "primary_completed_part_count": state["primary_completed_part_count"],
        "primary_part_count": state["primary_part_count"],
        "purpose": contract.PURPOSE,
        "run_id": request["run_id"],
        "run_status": state["status"],
        "season_number": contract.SEASON_NUMBER,
        "status": status,
    }
    try:
        return contract.validate_aggregate(value, status=status)
    except (TypeError, ValueError) as error:
        raise NextPrimaryObservationError("observation aggregate invalid") from error


def _safe_env() -> dict[str, str]:
    return {
        "PATH": "/usr/sbin:/usr/bin",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _container_identity_is_exact(runs: Path) -> bool:
    try:
        expected_image = submit.submission._release_image_reference()
        inspected = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .}}",
                host.REVIEW_NEXT_OBSERVATION_CONTAINER_NAME,
            ],
            cwd=RELEASE_ROOT,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            check=False,
            timeout=host.REVIEW_NEXT_OBSERVATION_KILL_AFTER_SECONDS,
        )
        if inspected.returncode != 0 or len(inspected.stdout) > MAX_RECORD_BYTES:
            return False
        value = json.loads(
            inspected.stdout.decode("utf-8"),
            object_pairs_hook=submit._unique_pairs,
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
        value.get("Name") != f"/{host.REVIEW_NEXT_OBSERVATION_CONTAINER_NAME}"
        or config.get("Image") != expected_image
        or config.get("User") != f"{WORKER_UID}:{WORKER_GID}"
        or config.get("WorkingDir") != host.REVIEW_NEXT_OBSERVATION_CONTAINER_WORKDIR
        or config.get("Cmd") != list(host.REVIEW_NEXT_OBSERVATION_CONTAINER_COMMAND)
        or not isinstance(labels, dict)
        or labels.get("com.docker.compose.service") != host.REVIEW_NEXT_OBSERVATION_COMPOSE_SERVICE
        or labels.get("com.docker.compose.oneoff") != "True"
        or labels.get("com.docker.compose.project") != host.REVIEW_NEXT_OBSERVATION_COMPOSE_PROJECT
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
        or set(networks) != {host.REVIEW_NEXT_OBSERVATION_NETWORK}
    ):
        return False
    destinations: dict[str, tuple[str, bool]] = {}
    for item in mounts:
        if not isinstance(item, dict):
            return False
        source = item.get("Source")
        destination = item.get("Destination")
        writable = item.get("RW")
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
        and destinations.get(host.REVIEW_NEXT_OBSERVATION_SECRET_TARGET, ("", True))[0] != ""
        and destinations.get(host.REVIEW_NEXT_OBSERVATION_SECRET_TARGET, ("", True))[1] is False
        and destinations.get(host.REVIEW_NEXT_OBSERVATION_TMP_TARGET) == ("", True)
        and set(destinations)
        == {
            WORKER_MOUNT,
            host.REVIEW_NEXT_OBSERVATION_SECRET_TARGET,
            host.REVIEW_NEXT_OBSERVATION_TMP_TARGET,
        }
    )


def _cleanup_worker(runs: Path) -> None:
    if not _container_identity_is_exact(runs):
        return
    try:
        subprocess.run(
            ["docker", "rm", "--force", host.REVIEW_NEXT_OBSERVATION_CONTAINER_NAME],
            cwd=RELEASE_ROOT,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            check=False,
            timeout=host.REVIEW_NEXT_OBSERVATION_KILL_AFTER_SECONDS,
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
            process.wait(timeout=host.REVIEW_NEXT_OBSERVATION_KILL_AFTER_SECONDS)
            return
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
    else:
        process.kill()
    process.wait()


def _worker_args(
    request: Mapping[str, object], run_parent: Path, binding: Mapping[str, object]
) -> list[str]:
    args = [
        "docker",
        "compose",
        "--progress",
        "quiet",
        "--env-file",
        os.fspath(ENV_FILE),
        "--profile",
        host.REVIEW_NEXT_OBSERVATION_COMPOSE_PROFILE,
        "-f",
        os.fspath(COMPOSE_PATH),
        "run",
        "--name",
        host.REVIEW_NEXT_OBSERVATION_CONTAINER_NAME,
        "--rm",
        "--no-TTY",
        "--no-deps",
        "--pull",
        "never",
        "--user",
        f"{WORKER_UID}:{WORKER_GID}",
        "--volume",
        f"{run_parent.as_posix()}:{WORKER_MOUNT}:rw",
    ]
    values = {
        contract.ENV_ARCHIVE_SHA256: request["archive_sha256"],
        contract.ENV_RUN_ID: request["run_id"],
        contract.ENV_AUTHORIZATION_ID: request["authorization_id"],
        contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: request["maximum_authorized_cost_microusd"],
        contract.ENV_EXPECTED_PRIMARY_PART_NUMBER: 2,
        contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: binding["pre_run_state_sha256"],
        contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: binding["pre_artifact_set_sha256"],
        contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: binding["pre_journal_set_sha256"],
        contract.ENV_EXPECTED_REQUEST_SHA256: binding["request_sha256"],
    }
    for name, value in values.items():
        args.extend(["--env", f"{name}={value}"])
    args.append(host.REVIEW_NEXT_OBSERVATION_COMPOSE_SERVICE)
    return args


def _run_worker(
    request: Mapping[str, object], run_parent: Path, binding: Mapping[str, object]
) -> dict[str, object]:
    process: subprocess.Popen[bytes] | None = None
    try:
        _cleanup_worker(run_parent)
        process = subprocess.Popen(
            _worker_args(request, run_parent, binding),
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
            stdout = pool.submit(process.stdout.read, observation.contract.OUTPUT_MAX_BYTES + 1)
            stderr = pool.submit(process.stderr.read, observation.contract.OUTPUT_MAX_BYTES + 1)
            code = process.wait(timeout=host.REVIEW_NEXT_OBSERVATION_TIMEOUT_SECONDS - 60)
            out, err = stdout.result(timeout=5), stderr.result(timeout=5)
        if code != 0 or err or len(out) > observation.contract.OUTPUT_MAX_BYTES:
            raise OSError
        return observation.contract.parse_aggregate(out)
    except (OSError, subprocess.SubprocessError, TimeoutError, TypeError, ValueError) as error:
        raise NextPrimaryObservationError("observation worker failed") from error
    finally:
        if process is not None and process.poll() is None:
            try:
                _terminate_worker(process)
            except (OSError, subprocess.SubprocessError):
                pass
        _cleanup_worker(run_parent)


def _write_receipt(path: Path, value: Mapping[str, object]) -> None:
    observation._write_once(path, value)


def _post_validate(
    before: Mapping[str, bytes],
    after: Mapping[str, bytes],
    before_state: Mapping[str, object],
    after_state: Mapping[str, object],
    status: str,
) -> None:
    if set(before_state) != set(after_state) or any(
        before_state[key] != after_state[key]
        for key in before_state.keys() - _MUTABLE_OBSERVATION_STATE_FIELDS
    ):
        raise NextPrimaryObservationError("observation immutable state changed")
    if any(
        name != STATE and (name not in after or _sha(after[name]) != _sha(raw))
        for name, raw in before.items()
    ):
        raise NextPrimaryObservationError("observation immutable evidence changed")
    added = set(after) - set(before)
    if status == "observed":
        if (
            added - PART2_OUTPUTS
            or after_state.get("status") != "primary_part_completed"
            or after_state.get("primary_completed_part_count") != 2
            or before_state.get("primary_completed_part_count") != 1
            or "primary-part-0002-output.jsonl" not in after
            or not submit._safe_text(after_state.get("updated_at"), maximum=128)
        ):
            raise NextPrimaryObservationError("observation post-state invalid")
    elif status == "failed":
        if (
            added - {TERMINAL_OUTPUT}
            or after_state.get("status") != "failed"
            or after_state.get("primary_completed_part_count") != 1
            or not submit._safe_text(after_state.get("updated_at"), maximum=128)
        ):
            raise NextPrimaryObservationError("observation post-state invalid")
    elif status in {"waiting", "reconciliation_required"}:
        if added or before != after or before_state != after_state:
            raise NextPrimaryObservationError("observation post-state invalid")
    else:
        raise NextPrimaryObservationError("observation worker status invalid")


def process_request(request: Mapping[str, object]) -> dict[str, object]:
    try:
        request = contract.validate_request(request)
    except (TypeError, ValueError) as error:
        raise NextPrimaryObservationError("invalid observation request") from error
    auth_sha = _validate_authorization(request)
    prep, _ = submit._validate_preparation(request)
    run = _run_directory(request)
    contents, state = _inventory(run, int(prep["result"]["primary_part_count"]))
    intent_path = observation._root_intent_path(str(request["run_id"]), 2)
    receipt_path = observation._root_receipt_path(str(request["run_id"]), 2)
    intent_exists = os.path.lexists(intent_path)
    receipt_exists = os.path.lexists(receipt_path)
    if receipt_exists and not intent_exists:
        raise NextPrimaryObservationError("orphan observation receipt")
    pre_state = (
        state.get("status") == "primary_submitted"
        and state.get("primary_completed_part_count") == 1
    )
    observed_state = (
        state.get("status") == "primary_part_completed"
        and state.get("primary_completed_part_count") == 2
    )
    failed_state = (
        state.get("status") == "failed" and state.get("primary_completed_part_count") == 1
    )
    if not (pre_state or observed_state or failed_state):
        raise NextPrimaryObservationError("observation checkpoint invalid")

    if pre_state:
        _validate_pre_state(state, prep)
        _validate_part2_journals(contents, state)
        (
            first_sha,
            first_observation_sha,
            _,
            phase63_artifact_sha,
            phase63_journal_sha,
            phase63_state_sha,
        ) = submit._validate_prior_evidence(request, prep, contents, state)
        next_intent_sha, next_sha = _validate_next_receipt(
            request,
            prep,
            contents,
            first_submission_sha256=first_sha,
            first_observation_sha256=first_observation_sha,
            phase63_artifact_sha256=phase63_artifact_sha,
            phase63_journal_sha256=phase63_journal_sha,
            phase63_state_sha256=phase63_state_sha,
        )
        if PART2_OUTPUTS & set(contents) or TERMINAL_OUTPUT in contents:
            raise NextPrimaryObservationError("observation pre-evidence changed")
        binding = _root_binding(
            request,
            prep,
            auth_sha,
            contents,
            state,
            first_sha,
            first_observation_sha,
            next_intent_sha,
            next_sha,
        )
        if intent_exists:
            intent = _validate_intent(
                observation._read_root_record(intent_path),
                request=request,
                prep=prep,
                authorization_sha256=auth_sha,
            )
            if intent != binding:
                raise NextPrimaryObservationError("observation intent binding changed")
        else:
            if receipt_exists:
                raise NextPrimaryObservationError("orphan observation receipt")
            intent = binding
            _write_receipt(intent_path, intent)
        _validate_checkpoint_against_intent(contents, state, intent)
    else:
        if not intent_exists:
            raise NextPrimaryObservationError("observation intent missing")
        intent = _validate_intent(
            observation._read_root_record(intent_path),
            request=request,
            prep=prep,
            authorization_sha256=auth_sha,
        )
        _validate_predecessors_from_intent(intent, request=request)
        terminal_status = "observed" if observed_state else "failed"
        _validate_recovered_transition(contents, state, intent, status=terminal_status)
        terminal_result = _aggregate(request, prep, state, terminal_status)
        if receipt_exists:
            _validate_final_receipt(
                observation._read_root_record(receipt_path),
                intent=intent,
                contents=contents,
                expected_status=terminal_status,
            )
        else:
            _write_receipt(
                receipt_path,
                _receipt_payload(intent, terminal_result, contents),
            )
        if terminal_status == "observed":
            return _aggregate(request, prep, state, "already_observed")
        return terminal_result

    if receipt_exists:
        raise NextPrimaryObservationError("observation receipt state invalid")
    before = contents
    before_state = state
    worker_result = _run_worker(request, run.parent, intent)
    after, after_state = _inventory(run, int(prep["result"]["primary_part_count"]))
    status_map = {
        "waiting": "waiting",
        "reconciliation_required": "reconciliation_required",
        "observed": "observed",
        "failed": "failed",
    }
    status = status_map.get(str(worker_result.get("status")))
    if (
        status is None
        or worker_result.get("run_id") != request["run_id"]
        or worker_result.get("primary_part_count") != prep["result"]["primary_part_count"]
        or worker_result.get("estimated_primary_cost_microusd") != prep["estimated"]
        or worker_result.get("primary_completed_part_count")
        != (2 if status in {"observed", "already_observed"} else 1)
        or worker_result.get("run_status")
        != (
            "primary_part_completed"
            if status == "observed"
            else "failed"
            if status == "failed"
            else "primary_submitted"
        )
    ):
        raise NextPrimaryObservationError("observation worker result invalid")
    _post_validate(before, after, before_state, after_state, status)
    if submit._active_binding() != (
        prep["release_sha"],
        prep["image"],
        prep["config_sha"],
    ):
        raise NextPrimaryObservationError("active runtime changed")
    if status in {"waiting", "reconciliation_required"}:
        return _aggregate(request, prep, after_state, status)
    result = _aggregate(request, prep, after_state, status)
    _write_receipt(receipt_path, _receipt_payload(intent, result, after))
    return result


def _require_root() -> None:
    if (
        os.name != "posix"
        or os.geteuid() != 0
        or os.environ.get("SUDO_USER") != host.REVIEW_USER
        or platform.system() != "Linux"
        or platform.machine() != "x86_64"
    ):
        raise NextPrimaryObservationError("invalid observation caller")


def main() -> int:
    try:
        _require_root()
        sys.stdout.buffer.write(
            contract.canonical_json(process_request(_read_request(sys.stdin.buffer)))
        )
        return 0
    except Exception:
        sys.stderr.write("error=speaker_review_next_primary_observation_rejected\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
