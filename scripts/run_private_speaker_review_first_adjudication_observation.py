"""Root-only coordinator for observing exactly adjudication part one.

It consumes the Phase 69 receipt, performs one provider read, and publishes a
new receipt only after the narrow filesystem transition has been verified.
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
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from typing import BinaryIO, Final, Mapping

_SCRIPTS = Path(__file__).resolve().parent
if os.fspath(_SCRIPTS) not in sys.path:
    sys.path.insert(0, os.fspath(_SCRIPTS))

try:
    from scripts import private_speaker_review_first_adjudication_observation_contract as contract
    from scripts import private_speaker_review_first_adjudication_observation_host_contract as host
    from scripts import (
        private_speaker_review_first_adjudication_submission_contract as submit_contract,
    )
    from scripts import run_private_speaker_review_first_adjudication as submit
    from scripts import run_private_speaker_review_observation as observation
except ModuleNotFoundError:
    import private_speaker_review_first_adjudication_observation_contract as contract
    import private_speaker_review_first_adjudication_observation_host_contract as host
    import private_speaker_review_first_adjudication_submission_contract as submit_contract
    import run_private_speaker_review_first_adjudication as submit
    import run_private_speaker_review_observation as observation


class FirstAdjudicationObservationError(RuntimeError):
    """Generic rejection that never exposes private/provider details."""


RELEASE_ROOT: Final = Path(__file__).resolve().parents[1]
RUNS_ROOT: Final = host.SPEAKER_REVIEW_RUNS_ROOT
AUTHORIZATION_ROOT: Final = host.REVIEW_AUTHORIZATION_ROOT
SUBMISSION_RECEIPTS_ROOT: Final = host.REVIEW_FIRST_ADJUDICATION_RECEIPTS_ROOT
OBSERVATION_RECEIPTS_ROOT: Final = host.REVIEW_FIRST_ADJUDICATION_OBSERVATION_RECEIPTS_ROOT
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
REQUEST: Final = "adjudication-part-0001-requests.jsonl"
SUBMISSION_INTENT: Final = ".adjudication-part-0001-submission-intent.json"
SUBMISSION_COMPLETED: Final = ".adjudication-part-0001-submission-completed.json"
OBSERVATION_OUTPUTS: Final = frozenset(
    {
        "adjudication-part-0001-output.jsonl",
        "adjudication-part-0001-api-errors.jsonl",
    }
)
TERMINAL_OUTPUT = "terminal-api-errors.jsonl"
_MUTABLE_OBSERVATION_STATE_FIELDS = frozenset(
    {"status", "updated_at", "adjudication_completed_part_count"}
)
_BINDING_KEYS = frozenset(
    {
        "archive_sha256",
        "actual_primary_cost_microusd",
        "adjudication_part_count",
        "authorization_id",
        "authorization_sha256",
        "configuration_sha256",
        "estimated_adjudication_cost_microusd",
        "image_reference",
        "maximum_authorized_cost_microusd",
        "first_adjudication_submission_intent_sha256",
        "first_adjudication_submission_receipt_sha256",
        "operation",
        "pre_artifact_set_sha256",
        "pre_derived_set_sha256",
        "pre_journal_set_sha256",
        "pre_output_set_sha256",
        "pre_run_state_sha256",
        "pre_state_binding_sha256",
        "prep_receipt_sha256",
        "purpose",
        "release_sha",
        "request_sha256",
        "run_id",
        "schema_version",
        "season_number",
        "status",
        "pre_updated_at",
    }
)
_RECEIPT_KEYS = frozenset(
    {
        *_BINDING_KEYS,
        "post_artifact_file_count",
        "post_artifact_set_sha256",
        "post_derived_file_count",
        "post_derived_set_sha256",
        "post_journal_file_count",
        "post_journal_set_sha256",
        "post_output_file_count",
        "post_output_set_sha256",
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


def _classes(
    contents: Mapping[str, bytes],
) -> tuple[dict[str, bytes], dict[str, bytes], dict[str, bytes], dict[str, bytes]]:
    artifacts: dict[str, bytes] = {}
    journals: dict[str, bytes] = {}
    outputs: dict[str, bytes] = {}
    derived: dict[str, bytes] = {}
    for name, raw in contents.items():
        if name == STATE:
            continue
        if name.startswith("."):
            journals[name] = raw
        elif name.endswith("-output.jsonl") or name.endswith("-api-errors.jsonl"):
            outputs[name] = raw
        elif name.endswith("-requests.jsonl") or name in {
            "candidates.jsonl",
            "source-manifest.json",
        }:
            artifacts[name] = raw
        else:
            derived[name] = raw
    return artifacts, journals, outputs, derived


def _micros(value: object) -> int:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise FirstAdjudicationObservationError("observation cost invalid")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise FirstAdjudicationObservationError("observation cost invalid") from error
    if not decimal.is_finite() or decimal < 0:
        raise FirstAdjudicationObservationError("observation cost invalid")
    result = int((decimal * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
    if result > contract.MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise FirstAdjudicationObservationError("observation cost invalid")
    return result


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
        raise FirstAdjudicationObservationError("observation evidence unavailable") from error


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
        raise FirstAdjudicationObservationError("observation evidence unavailable") from error
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
        raise FirstAdjudicationObservationError("observation evidence changed")
    return raw


def _decode(raw: bytes) -> dict[str, object]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=contract._pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise FirstAdjudicationObservationError("observation evidence invalid") from error
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise FirstAdjudicationObservationError("observation evidence invalid")
    return value


def _read_request(stream: BinaryIO) -> dict[str, object]:
    raw = stream.readline(contract.REQUEST_MAX_BYTES + 1)
    if stream.read(1):
        raise FirstAdjudicationObservationError("invalid observation request")
    try:
        return contract.parse_request(raw)
    except (TypeError, ValueError) as error:
        raise FirstAdjudicationObservationError("invalid observation request") from error


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
        raise FirstAdjudicationObservationError("observation authorization invalid") from error
    return _sha(raw)


def _run_directory(request: Mapping[str, object]) -> Path:
    _directory(RUNS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    object_root = RUNS_ROOT / f"sha256-{request['archive_sha256']}"
    runs = object_root / "review-runs"
    _directory(object_root, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    _directory(runs, mode=0o700, owner=(WORKER_UID, WORKER_GID))
    path = runs / str(request["run_id"])
    if path.resolve(strict=False).parent != runs.resolve(strict=True):
        raise FirstAdjudicationObservationError("observation run invalid")
    _directory(path, mode=0o700, owner=(WORKER_UID, WORKER_GID))
    return path


def _inventory(run: Path) -> tuple[dict[str, bytes], dict[str, object]]:
    try:
        contents, _, _, _, extra = submit._inventory(run)
        state = extra["state"]
    except Exception as error:
        raise FirstAdjudicationObservationError("observation inventory invalid") from error
    if not isinstance(state, dict) or set(state) != observation._RUN_STATE_KEYS:
        raise FirstAdjudicationObservationError("observation run state invalid")
    return contents, state


def _validate_submitted_state(state: Mapping[str, object]) -> None:
    try:
        batch_ids = state["adjudication_batch_ids"]
        input_ids = state["adjudication_input_file_ids"]
        if (
            state["status"] != "adjudication_submitted"
            or type(state["adjudication_part_count"]) is not int
            or state["adjudication_part_count"] <= 0
            or state["adjudication_completed_part_count"] != 0
            or not isinstance(batch_ids, list)
            or not isinstance(input_ids, list)
            or len(batch_ids) != 1
            or len(input_ids) != 1
            or state["adjudication_batch_id"] != batch_ids[0]
            or state["adjudication_input_file_id"] != input_ids[0]
            or not all(observation._safe_text(value) for value in (*batch_ids, *input_ids))
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise FirstAdjudicationObservationError("observation checkpoint invalid") from error


def _validate_submission_predecessor(
    request: Mapping[str, object],
    run: Path,
    contents: Mapping[str, bytes],
    state: Mapping[str, object],
) -> tuple[dict[str, object], str, str, str, int]:
    """Validate the complete Phase 69 submission evidence chain."""

    try:
        _validate_submitted_state(state)
        preparation, preparation_sha = submit._preparation(request)
        submit.phase68._source_workspace(request)
        _directory(SUBMISSION_RECEIPTS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
        intent, intent_sha = submit._record(
            SUBMISSION_RECEIPTS_ROOT / f"{request['run_id']}.intent.json"
        )
        receipt, receipt_sha = submit._record(
            SUBMISSION_RECEIPTS_ROOT / f"{request['run_id']}.json"
        )
        if set(intent) != submit._ROOT_INTENT_KEYS or set(receipt) != submit._ROOT_RECEIPT_KEYS:
            raise ValueError
        phase69_auth_id = intent["authorization_id"]
        if not isinstance(phase69_auth_id, str):
            raise ValueError
        phase69_auth_raw = _stable(
            AUTHORIZATION_ROOT / f"{phase69_auth_id}.json",
            maximum=submit_contract.REQUEST_MAX_BYTES,
            mode=0o600,
            owner=(ROOT_UID, ROOT_GID),
        )
        phase69_request = submit_contract.parse_request(phase69_auth_raw)
        expected_phase69_request = {
            "archive_sha256": request["archive_sha256"],
            "authorization_id": phase69_auth_id,
            "maximum_authorized_cost_microusd": intent["maximum_authorized_cost_microusd"],
            "operation": submit_contract.OPERATION,
            "purpose": contract.PURPOSE,
            "run_id": request["run_id"],
            "schema_version": submit_contract.PROTOCOL_VERSION,
            "season_number": contract.SEASON_NUMBER,
        }
        if phase69_request != expected_phase69_request:
            raise ValueError

        phase69_files, phase69_artifacts, phase69_journals, phase69_outputs, extra = (
            submit._inventory(run)
        )
        if phase69_files != contents or extra["state"] != state:
            raise ValueError
        phase69_derived = extra["derived"]
        new_journals = {
            name: raw
            for name, raw in phase69_journals.items()
            if name in {SUBMISSION_INTENT, SUBMISSION_COMPLETED}
        }
        if set(new_journals) != {SUBMISSION_INTENT, SUBMISSION_COMPLETED}:
            raise ValueError
        original_journals = {
            name: raw for name, raw in phase69_journals.items() if name not in new_journals
        }
        prepared = submit._prepared_state(state, intent)
        prepared_raw = _canonical(prepared)
        prior_files = {**phase69_files, STATE: prepared_raw}
        for name in new_journals:
            prior_files.pop(name)
        processing_sha, _ = submit._phase68(
            request,
            run,
            prepared,
            prior_files,
            phase69_artifacts,
            original_journals,
            phase69_outputs,
            phase69_derived,
            preparation,
        )
        submit._validate_pre_adjudication(prepared, phase69_files[REQUEST])
        expected_intent = {
            "archive_sha256": request["archive_sha256"],
            "authorization_id": phase69_auth_id,
            "authorization_sha256": _sha(phase69_auth_raw),
            "configuration_sha256": preparation["config_sha"],
            "image_reference": preparation["image"],
            "maximum_authorized_cost_microusd": intent["maximum_authorized_cost_microusd"],
            "operation": submit_contract.OPERATION,
            "prep_receipt_sha256": preparation_sha,
            "processing_receipt_sha256": processing_sha,
            "purpose": contract.PURPOSE,
            "release_sha": preparation["release_sha"],
            "run_id": request["run_id"],
            "request_sha256": _sha(phase69_files[REQUEST]),
            "schema_version": submit_contract.PROTOCOL_VERSION,
            "season_number": contract.SEASON_NUMBER,
            "status": "intent",
            "pre_updated_at": prepared["updated_at"],
            "pre_state_sha256": _sha(prepared_raw),
            "pre_artifact_set_sha256": _set_digest(phase69_artifacts),
            "pre_journal_set_sha256": _set_digest(original_journals),
            "pre_output_set_sha256": _set_digest(phase69_outputs),
            "pre_derived_set_sha256": _set_digest(phase69_derived),
        }
        if intent != expected_intent:
            raise ValueError
        expected_result = submit._expected_result(
            state,
            receipt["result"],
            int(intent["maximum_authorized_cost_microusd"]),
            "submitted",
        )
        expected_receipt = {
            **intent,
            "status": "receipt",
            "post_state_sha256": _sha(contents[STATE]),
            "post_journal_set_sha256": _set_digest(phase69_journals),
            "result": expected_result,
        }
        if receipt != expected_receipt:
            raise ValueError
        submit._validate_submission_journals(
            state,
            phase69_journals,
            _sha(contents[REQUEST]),
        )
        if OBSERVATION_OUTPUTS & set(contents) or TERMINAL_OUTPUT in contents:
            raise ValueError
        estimated = expected_result["estimated_adjudication_cost_microusd"]
        if type(estimated) is not int:
            raise ValueError
        return preparation, preparation_sha, intent_sha, receipt_sha, estimated
    except FirstAdjudicationObservationError:
        raise
    except Exception as error:
        raise FirstAdjudicationObservationError(
            "first-adjudication submission evidence invalid"
        ) from error


def _root_binding(
    request: Mapping[str, object],
    authorization_sha256: str,
    preparation: Mapping[str, object],
    preparation_sha256: str,
    submission_intent_sha256: str,
    submission_receipt_sha256: str,
    estimated_adjudication_cost_microusd: int,
    contents: Mapping[str, bytes],
    state: Mapping[str, object],
) -> dict[str, object]:
    artifacts, journals, outputs, derived = _classes(contents)
    return {
        "archive_sha256": request["archive_sha256"],
        "actual_primary_cost_microusd": _micros(state["actual_primary_cost_usd"]),
        "adjudication_part_count": state["adjudication_part_count"],
        "authorization_id": request["authorization_id"],
        "authorization_sha256": authorization_sha256,
        "configuration_sha256": preparation["config_sha"],
        "estimated_adjudication_cost_microusd": estimated_adjudication_cost_microusd,
        "first_adjudication_submission_intent_sha256": submission_intent_sha256,
        "first_adjudication_submission_receipt_sha256": submission_receipt_sha256,
        "image_reference": preparation["image"],
        "maximum_authorized_cost_microusd": request["maximum_authorized_cost_microusd"],
        "operation": contract.OPERATION,
        "pre_artifact_set_sha256": _set_digest(artifacts),
        "pre_derived_set_sha256": _set_digest(derived),
        "pre_journal_set_sha256": _set_digest(journals),
        "pre_output_set_sha256": _set_digest(outputs),
        "pre_run_state_sha256": _sha(contents[STATE]),
        "pre_state_binding_sha256": _state_binding_sha256(state),
        "pre_updated_at": state["updated_at"],
        "prep_receipt_sha256": preparation_sha256,
        "purpose": contract.PURPOSE,
        "release_sha": preparation["release_sha"],
        "request_sha256": _sha(contents[REQUEST]),
        "run_id": request["run_id"],
        "schema_version": contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
        "status": "intent",
    }


def _validate_intent(
    value: object,
    *,
    request: Mapping[str, object],
    authorization_sha256: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _BINDING_KEYS:
        raise FirstAdjudicationObservationError("observation intent invalid")
    actual = value.get("actual_primary_cost_microusd")
    estimate = value.get("estimated_adjudication_cost_microusd")
    maximum = request.get("maximum_authorized_cost_microusd")
    if (
        value.get("archive_sha256") != request["archive_sha256"]
        or value.get("authorization_id") != request["authorization_id"]
        or value.get("authorization_sha256") != authorization_sha256
        or value.get("maximum_authorized_cost_microusd")
        != request["maximum_authorized_cost_microusd"]
        or value.get("operation") != contract.OPERATION
        or value.get("purpose") != contract.PURPOSE
        or value.get("run_id") != request["run_id"]
        or value.get("schema_version") != contract.PROTOCOL_VERSION
        or value.get("season_number") != contract.SEASON_NUMBER
        or value.get("status") != "intent"
        or type(actual) is not int
        or type(estimate) is not int
        or type(maximum) is not int
        or actual < 0
        or estimate < 0
        or actual + estimate > maximum
        or type(value.get("adjudication_part_count")) is not int
        or value["adjudication_part_count"] <= 0
        or not observation._safe_text(value.get("pre_updated_at"), maximum=128)
    ):
        raise FirstAdjudicationObservationError("observation intent invalid")
    for key in (
        "configuration_sha256",
        "first_adjudication_submission_intent_sha256",
        "first_adjudication_submission_receipt_sha256",
        "pre_artifact_set_sha256",
        "pre_derived_set_sha256",
        "pre_journal_set_sha256",
        "pre_output_set_sha256",
        "pre_run_state_sha256",
        "pre_state_binding_sha256",
        "prep_receipt_sha256",
        "request_sha256",
    ):
        if not _is_sha(value.get(key)):
            raise FirstAdjudicationObservationError("observation intent invalid")
    if not observation._safe_text(
        value.get("release_sha"), maximum=40
    ) or not observation._safe_text(value.get("image_reference"), maximum=512):
        raise FirstAdjudicationObservationError("observation intent invalid")
    return dict(value)


def _validate_predecessors_from_intent(
    intent: Mapping[str, object], request: Mapping[str, object]
) -> None:
    try:
        _, preparation_sha = submit._preparation(request)
        submit.phase68._source_workspace(request)
        _, submission_intent_sha = submit._record(
            SUBMISSION_RECEIPTS_ROOT / f"{request['run_id']}.intent.json"
        )
        _, submission_receipt_sha = submit._record(
            SUBMISSION_RECEIPTS_ROOT / f"{request['run_id']}.json"
        )
    except Exception as error:
        raise FirstAdjudicationObservationError(
            "observation predecessor evidence invalid"
        ) from error
    if (
        intent.get("prep_receipt_sha256") != preparation_sha
        or intent.get("first_adjudication_submission_intent_sha256") != submission_intent_sha
        or intent.get("first_adjudication_submission_receipt_sha256") != submission_receipt_sha
        or submit.predecessor._active_binding()
        != (
            intent.get("release_sha"),
            intent.get("image_reference"),
            intent.get("configuration_sha256"),
        )
    ):
        raise FirstAdjudicationObservationError("observation predecessor evidence changed")


def _validate_checkpoint_against_intent(
    contents: Mapping[str, bytes],
    state: Mapping[str, object],
    intent: Mapping[str, object],
) -> None:
    artifacts, journals, outputs, derived = _classes(contents)
    if (
        intent.get("pre_run_state_sha256") != _sha(contents[STATE])
        or intent.get("pre_state_binding_sha256") != _state_binding_sha256(state)
        or intent.get("pre_artifact_set_sha256") != _set_digest(artifacts)
        or intent.get("pre_journal_set_sha256") != _set_digest(journals)
        or intent.get("pre_output_set_sha256") != _set_digest(outputs)
        or intent.get("pre_derived_set_sha256") != _set_digest(derived)
        or intent.get("request_sha256") != _sha(contents[REQUEST])
    ):
        raise FirstAdjudicationObservationError("observation checkpoint changed")


def _pre_observation_snapshot(
    contents: Mapping[str, bytes],
    state: Mapping[str, object],
    intent: Mapping[str, object],
) -> tuple[dict[str, bytes], dict[str, object]]:
    before_state = dict(state)
    before_state.update(
        status="adjudication_submitted",
        updated_at=intent["pre_updated_at"],
        adjudication_completed_part_count=0,
    )
    before = {
        name: raw
        for name, raw in contents.items()
        if name not in OBSERVATION_OUTPUTS and name != TERMINAL_OUTPUT
    }
    before[STATE] = _canonical(before_state)
    _validate_checkpoint_against_intent(before, before_state, intent)
    return before, before_state


def _aggregate(
    request: Mapping[str, object],
    state: Mapping[str, object],
    status: str,
    estimated_adjudication_cost_microusd: int,
) -> dict[str, object]:
    value = {
        "actual_primary_cost_microusd": _micros(state["actual_primary_cost_usd"]),
        "adjudication_completed_part_count": state["adjudication_completed_part_count"],
        "adjudication_part_count": state["adjudication_part_count"],
        "estimated_adjudication_cost_microusd": estimated_adjudication_cost_microusd,
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": request["run_id"],
        "run_status": state["status"],
        "season_number": contract.SEASON_NUMBER,
        "status": status,
    }
    try:
        return contract.validate_aggregate(value, status=status)
    except (TypeError, ValueError) as error:
        raise FirstAdjudicationObservationError("observation aggregate invalid") from error


def _receipt_payload(
    intent: Mapping[str, object],
    result: Mapping[str, object],
    contents: Mapping[str, bytes],
) -> dict[str, object]:
    artifacts, journals, outputs, derived = _classes(contents)
    return {
        **intent,
        "status": "receipt",
        "post_artifact_file_count": len(artifacts),
        "post_artifact_set_sha256": _set_digest(artifacts),
        "post_derived_file_count": len(derived),
        "post_derived_set_sha256": _set_digest(derived),
        "post_journal_file_count": len(journals),
        "post_journal_set_sha256": _set_digest(journals),
        "post_output_file_count": len(outputs),
        "post_output_set_sha256": _set_digest(outputs),
        "post_run_state_sha256": _sha(contents[STATE]),
        "result": dict(result),
    }


def _validate_final_receipt(
    value: object,
    *,
    intent: Mapping[str, object],
    result: Mapping[str, object],
    contents: Mapping[str, bytes],
) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != _RECEIPT_KEYS
        or value != _receipt_payload(intent, result, contents)
    ):
        raise FirstAdjudicationObservationError("observation receipt invalid")


def _safe_env() -> dict[str, str]:
    return {
        "PATH": "/usr/sbin:/usr/bin",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _container_identity_is_exact(runs: Path) -> bool:
    try:
        expected_image = submit.predecessor._active_binding()[1]
        inspected = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .}}",
                host.REVIEW_FIRST_ADJUDICATION_OBSERVATION_CONTAINER_NAME,
            ],
            cwd=RELEASE_ROOT,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            check=False,
            timeout=host.REVIEW_FIRST_ADJUDICATION_OBSERVATION_KILL_AFTER_SECONDS,
        )
        if inspected.returncode != 0 or len(inspected.stdout) > MAX_RECORD_BYTES:
            return False
        value = json.loads(
            inspected.stdout.decode("utf-8"),
            object_pairs_hook=contract._pairs,
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
        value.get("Name") != f"/{host.REVIEW_FIRST_ADJUDICATION_OBSERVATION_CONTAINER_NAME}"
        or config.get("Image") != expected_image
        or config.get("User") != f"{WORKER_UID}:{WORKER_GID}"
        or config.get("WorkingDir") != host.REVIEW_FIRST_ADJUDICATION_OBSERVATION_CONTAINER_WORKDIR
        or config.get("Cmd") != list(host.REVIEW_FIRST_ADJUDICATION_OBSERVATION_CONTAINER_COMMAND)
        or not isinstance(labels, dict)
        or labels.get("com.docker.compose.service")
        != host.REVIEW_FIRST_ADJUDICATION_OBSERVATION_COMPOSE_SERVICE
        or labels.get("com.docker.compose.oneoff") != "True"
        or labels.get("com.docker.compose.project")
        != host.REVIEW_FIRST_ADJUDICATION_OBSERVATION_COMPOSE_PROJECT
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
        or set(networks) != {host.REVIEW_FIRST_ADJUDICATION_OBSERVATION_NETWORK}
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
        and destinations.get(host.REVIEW_FIRST_ADJUDICATION_OBSERVATION_SECRET_TARGET, ("", True))[
            0
        ]
        != ""
        and destinations.get(host.REVIEW_FIRST_ADJUDICATION_OBSERVATION_SECRET_TARGET, ("", True))[
            1
        ]
        is False
        and destinations.get(host.REVIEW_FIRST_ADJUDICATION_OBSERVATION_TMP_TARGET) == ("", True)
        and set(destinations)
        == {
            WORKER_MOUNT,
            host.REVIEW_FIRST_ADJUDICATION_OBSERVATION_SECRET_TARGET,
            host.REVIEW_FIRST_ADJUDICATION_OBSERVATION_TMP_TARGET,
        }
    )


def _cleanup_worker(runs: Path) -> None:
    if not _container_identity_is_exact(runs):
        return
    try:
        subprocess.run(
            ["docker", "rm", "--force", host.REVIEW_FIRST_ADJUDICATION_OBSERVATION_CONTAINER_NAME],
            cwd=RELEASE_ROOT,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            check=False,
            timeout=host.REVIEW_FIRST_ADJUDICATION_OBSERVATION_KILL_AFTER_SECONDS,
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
            process.wait(timeout=host.REVIEW_FIRST_ADJUDICATION_OBSERVATION_KILL_AFTER_SECONDS)
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
        host.REVIEW_FIRST_ADJUDICATION_OBSERVATION_COMPOSE_PROFILE,
        "-f",
        os.fspath(COMPOSE_PATH),
        "run",
        "--name",
        host.REVIEW_FIRST_ADJUDICATION_OBSERVATION_CONTAINER_NAME,
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
        contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: binding["pre_run_state_sha256"],
        contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: binding["pre_artifact_set_sha256"],
        contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: binding["pre_journal_set_sha256"],
        contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256: binding["pre_output_set_sha256"],
        contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256: binding["pre_derived_set_sha256"],
        contract.ENV_EXPECTED_REQUEST_SHA256: binding["request_sha256"],
    }
    for name, value in values.items():
        args.extend(["--env", f"{name}={value}"])
    args.append(host.REVIEW_FIRST_ADJUDICATION_OBSERVATION_COMPOSE_SERVICE)
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
            stdout = pool.submit(process.stdout.read, contract.OUTPUT_MAX_BYTES + 1)
            stderr = pool.submit(process.stderr.read, contract.OUTPUT_MAX_BYTES + 1)
            code = process.wait(
                timeout=host.REVIEW_FIRST_ADJUDICATION_OBSERVATION_TIMEOUT_SECONDS - 60
            )
            out, err = stdout.result(timeout=5), stderr.result(timeout=5)
        if code != 0 or err or len(out) > contract.OUTPUT_MAX_BYTES:
            raise OSError
        return contract.parse_aggregate(out)
    except (OSError, subprocess.SubprocessError, TimeoutError, TypeError, ValueError) as error:
        raise FirstAdjudicationObservationError("observation worker failed") from error
    finally:
        if process is not None and process.poll() is None:
            try:
                _terminate_worker(process)
            except (OSError, subprocess.SubprocessError):
                pass
        _cleanup_worker(run_parent)


def _write_receipt(path: Path, value: Mapping[str, object]) -> None:
    _directory(OBSERVATION_RECEIPTS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    raw = _canonical(value)
    if len(raw) > MAX_RECORD_BYTES:
        raise FirstAdjudicationObservationError("observation receipt too large")
    pending = path.with_name(f".{path.name}.pending")
    if os.path.lexists(path):
        submit._repair_linked_publication(path)
        existing, _ = _read_record(path)
        if existing != dict(value):
            raise FirstAdjudicationObservationError("observation receipt conflict")
        return
    if os.path.lexists(pending):
        if (
            _stable(
                pending,
                maximum=MAX_RECORD_BYTES,
                mode=0o600,
                owner=(ROOT_UID, ROOT_GID),
            )
            != raw
        ):
            raise FirstAdjudicationObservationError("observation receipt conflict")
    else:
        descriptor = -1
        try:
            descriptor = os.open(
                pending,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            submit._fsync_directory(OBSERVATION_RECEIPTS_ROOT)
        except OSError as error:
            raise FirstAdjudicationObservationError("observation receipt unavailable") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    try:
        os.link(pending, path, follow_symlinks=False)
        submit._fsync_directory(OBSERVATION_RECEIPTS_ROOT)
        pending.unlink()
        submit._fsync_directory(OBSERVATION_RECEIPTS_ROOT)
    except FileExistsError:
        existing, _ = _read_record(path)
        if existing != dict(value):
            raise FirstAdjudicationObservationError("observation receipt conflict") from None
        pending.unlink(missing_ok=True)
    except OSError as error:
        raise FirstAdjudicationObservationError("observation receipt unavailable") from error


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
        raise FirstAdjudicationObservationError("observation immutable state changed")
    if any(
        name != STATE and (name not in after or _sha(after[name]) != _sha(raw))
        for name, raw in before.items()
    ):
        raise FirstAdjudicationObservationError("observation immutable evidence changed")
    added = set(after) - set(before)
    if status == "observed":
        if (
            added - OBSERVATION_OUTPUTS
            or after_state.get("status") != "adjudication_part_completed"
            or after_state.get("adjudication_completed_part_count") != 1
            or before_state.get("adjudication_completed_part_count") != 0
            or "adjudication-part-0001-output.jsonl" not in after
            or not observation._safe_text(after_state.get("updated_at"), maximum=128)
        ):
            raise FirstAdjudicationObservationError("observation post-state invalid")
    elif status == "failed":
        if (
            added - {TERMINAL_OUTPUT}
            or after_state.get("status") != "failed"
            or after_state.get("adjudication_completed_part_count") != 0
            or not observation._safe_text(after_state.get("updated_at"), maximum=128)
        ):
            raise FirstAdjudicationObservationError("observation post-state invalid")
    elif status in {"waiting", "reconciliation_required"}:
        if added or before != after or before_state != after_state:
            raise FirstAdjudicationObservationError("observation post-state invalid")
    else:
        raise FirstAdjudicationObservationError("observation worker status invalid")


def process_request(request: Mapping[str, object]) -> dict[str, object]:
    try:
        request = contract.validate_request(request)
    except (TypeError, ValueError) as error:
        raise FirstAdjudicationObservationError("invalid observation request") from error

    authorization_sha256 = _validate_authorization(request)
    run = _run_directory(request)
    _directory(OBSERVATION_RECEIPTS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    contents, state = _inventory(run)
    intent_path = OBSERVATION_RECEIPTS_ROOT / f"{request['run_id']}.intent.json"
    receipt_path = OBSERVATION_RECEIPTS_ROOT / f"{request['run_id']}.json"
    intent_exists = os.path.lexists(intent_path)
    receipt_exists = os.path.lexists(receipt_path)
    if receipt_exists and not intent_exists:
        raise FirstAdjudicationObservationError("orphan observation receipt")

    submitted = (
        state.get("status") == "adjudication_submitted"
        and state.get("adjudication_completed_part_count") == 0
    )
    observed = (
        state.get("status") == "adjudication_part_completed"
        and state.get("adjudication_completed_part_count") == 1
    )
    failed = state.get("status") == "failed" and state.get("adjudication_completed_part_count") == 0
    if not (submitted or observed or failed):
        raise FirstAdjudicationObservationError("observation checkpoint invalid")

    if observed or failed:
        if not intent_exists:
            raise FirstAdjudicationObservationError("observation intent missing")
        intent = _validate_intent(
            _read_record(intent_path)[0],
            request=request,
            authorization_sha256=authorization_sha256,
        )
        _validate_predecessors_from_intent(intent, request)
        before, before_state = _pre_observation_snapshot(contents, state, intent)
        terminal_status = "observed" if observed else "failed"
        _post_validate(before, contents, before_state, state, terminal_status)
        estimate = intent["estimated_adjudication_cost_microusd"]
        if type(estimate) is not int:
            raise FirstAdjudicationObservationError("observation intent invalid")
        result = _aggregate(request, state, terminal_status, estimate)
        expected_receipt = _receipt_payload(intent, result, contents)
        if receipt_exists:
            _validate_final_receipt(
                _read_record(receipt_path)[0],
                intent=intent,
                result=result,
                contents=contents,
            )
        else:
            _write_receipt(receipt_path, expected_receipt)
        if observed:
            return _aggregate(request, state, "already_observed", estimate)
        return result

    _validate_submitted_state(state)
    (
        preparation,
        preparation_sha,
        submission_intent_sha,
        submission_receipt_sha,
        estimate,
    ) = _validate_submission_predecessor(request, run, contents, state)
    actual = _micros(state["actual_primary_cost_usd"])
    if actual + estimate > request["maximum_authorized_cost_microusd"]:
        raise FirstAdjudicationObservationError("observation cost exceeds authorization")
    binding = _root_binding(
        request,
        authorization_sha256,
        preparation,
        preparation_sha,
        submission_intent_sha,
        submission_receipt_sha,
        estimate,
        contents,
        state,
    )
    if intent_exists:
        intent = _validate_intent(
            _read_record(intent_path)[0],
            request=request,
            authorization_sha256=authorization_sha256,
        )
        if intent != binding:
            raise FirstAdjudicationObservationError("observation intent binding changed")
    else:
        if receipt_exists:
            raise FirstAdjudicationObservationError("orphan observation receipt")
        intent = binding
        _write_receipt(intent_path, intent)
    if receipt_exists:
        raise FirstAdjudicationObservationError("observation receipt state invalid")
    _validate_checkpoint_against_intent(contents, state, intent)
    _validate_predecessors_from_intent(intent, request)

    before, before_state = contents, state
    worker_result = _run_worker(request, run.parent, intent)
    after, after_state = _inventory(run)
    status = str(worker_result.get("status"))
    if status not in {"waiting", "observed", "failed", "reconciliation_required"}:
        raise FirstAdjudicationObservationError("observation worker result invalid")
    expected_completed = 1 if status == "observed" else 0
    expected_run_status = (
        "adjudication_part_completed"
        if status == "observed"
        else "failed"
        if status == "failed"
        else "adjudication_submitted"
    )
    if (
        worker_result.get("run_id") != request["run_id"]
        or worker_result.get("actual_primary_cost_microusd") != actual
        or worker_result.get("estimated_adjudication_cost_microusd") != estimate
        or worker_result.get("adjudication_part_count") != state["adjudication_part_count"]
        or worker_result.get("adjudication_completed_part_count") != expected_completed
        or worker_result.get("run_status") != expected_run_status
    ):
        raise FirstAdjudicationObservationError("observation worker result invalid")
    _post_validate(before, after, before_state, after_state, status)
    if submit.predecessor._active_binding() != (
        preparation["release_sha"],
        preparation["image"],
        preparation["config_sha"],
    ):
        raise FirstAdjudicationObservationError("active runtime changed")
    submit.phase68._source_workspace(request)
    result = _aggregate(request, after_state, status, estimate)
    if status in {"waiting", "reconciliation_required"}:
        return result
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
        raise FirstAdjudicationObservationError("invalid observation caller")


def main() -> int:
    try:
        _require_root()
        sys.stdout.buffer.write(
            contract.canonical_json(process_request(_read_request(sys.stdin.buffer)))
        )
        return 0
    except Exception:
        sys.stderr.write("error=speaker_review_first_adjudication_observation_rejected\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
