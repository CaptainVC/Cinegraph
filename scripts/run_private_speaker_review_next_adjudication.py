"""Root-only coordinator for exactly one subsequent adjudication submission.

The coordinator is the host half of the Phase 71 boundary. It authenticates
one canonical request, verifies the Phase 70 observation chain and the
digest-selected run, then starts one constrained Compose worker for exactly
adjudication part two. The application transition remains generic, but this
host command never observes output, submits part three, parses a result,
advances review, enters final review, promotes, or ingests.
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
from dataclasses import replace
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from typing import BinaryIO, Final, Mapping

_ROOT = Path(__file__).resolve().parents[1]
if os.fspath(_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(_ROOT))

from cinegraph.config import DEFAULT_SPEAKER_REVIEW_CONFIGURATION  # noqa: E402
from cinegraph.domain.enums.enum import SpeakerReviewRunStatus  # noqa: E402
from cinegraph.ingestion.speaker_review.workflow import (  # noqa: E402
    SpeakerReviewRunState,
    load_validated_run_state,
)
from scripts import (  # noqa: E402
    private_speaker_review_first_adjudication_host_contract as phase69_host,
)
from scripts import (  # noqa: E402
    private_speaker_review_first_adjudication_observation_contract as phase70_contract,
)
from scripts import (  # noqa: E402
    private_speaker_review_first_adjudication_observation_host_contract as phase70_host,
)
from scripts import (  # noqa: E402
    private_speaker_review_next_adjudication_host_contract as host,
)
from scripts import (  # noqa: E402
    private_speaker_review_next_adjudication_submission_contract as contract,
)
from scripts import (  # noqa: E402
    run_private_speaker_review_first_adjudication as phase69,
)
from scripts import (  # noqa: E402
    run_private_speaker_review_first_adjudication_observation as phase70,
)
from scripts import (  # noqa: E402
    run_private_speaker_review_next_primary as preparation,
)
from scripts import (  # noqa: E402
    submit_next_private_speaker_review_adjudication_workspace as worker,
)


class NextAdjudicationSubmissionError(RuntimeError):
    """Generic path-free rejection at the privileged host boundary."""


# A compatibility spelling makes the operation easy to discover beside the
# Phase 69/70 coordinators without changing the public error surface.
NextAdjudicationProcessingError = NextAdjudicationSubmissionError

RELEASE_ROOT: Final = _ROOT
RUNS_ROOT: Final = host.SPEAKER_REVIEW_RUNS_ROOT
AUTHORIZATION_ROOT: Final = host.REVIEW_AUTHORIZATION_ROOT
PREPARATION_RECEIPTS_ROOT: Final = host.SPEAKER_REVIEW_ROOT / "receipts"
PHASE69_RECEIPTS_ROOT: Final = phase69_host.REVIEW_FIRST_ADJUDICATION_RECEIPTS_ROOT
PHASE70_RECEIPTS_ROOT: Final = phase70_host.REVIEW_FIRST_ADJUDICATION_OBSERVATION_RECEIPTS_ROOT
RECEIPTS_ROOT: Final = host.REVIEW_NEXT_ADJUDICATION_RECEIPTS_ROOT
ENV_FILE: Final = host.ENV_FILE
COMPOSE_PATH: Final = RELEASE_ROOT / "deploy/compose.yaml"
ROOT_UID: Final = 0
ROOT_GID: Final = 0
WORKER_UID: Final = host.REVIEW_NEXT_ADJUDICATION_WORKER_UID
WORKER_GID: Final = host.REVIEW_NEXT_ADJUDICATION_WORKER_GID
MAX_RECORD_BYTES: Final = 128 * 1024
MAX_TOTAL_BYTES: Final = 256 * 1024 * 1024
STATE_NAME: Final = "run-state.json"
ROOT_STATE_MUTABLE: Final = frozenset(
    {
        "status",
        "updated_at",
        "adjudication_batch_id",
        "adjudication_input_file_id",
        "adjudication_batch_ids",
        "adjudication_input_file_ids",
    }
)
_INTENT_NAME = ".adjudication-part-{part:04d}-submission-intent.json"
_COMPLETED_NAME = ".adjudication-part-{part:04d}-submission-completed.json"
_OUTPUT_NAME = "adjudication-part-{part:04d}-output.jsonl"
_API_ERRORS_NAME = "adjudication-part-{part:04d}-api-errors.jsonl"

_ROOT_INTENT_KEYS = frozenset(
    {
        "actual_primary_cost_microusd",
        "adjudication_completed_part_count",
        "adjudication_part_count",
        "archive_sha256",
        "authorization_id",
        "authorization_sha256",
        "configuration_sha256",
        "estimated_adjudication_cost_microusd",
        "image_reference",
        "maximum_authorized_cost_microusd",
        "next_adjudication_part_number",
        "next_request_sha256",
        "operation",
        "phase70_observation_intent_sha256",
        "phase70_observation_receipt_sha256",
        "pre_artifact_set_sha256",
        "pre_derived_set_sha256",
        "pre_journal_set_sha256",
        "pre_output_set_sha256",
        "pre_run_state_sha256",
        "pre_state_binding_sha256",
        "pre_updated_at",
        "prep_receipt_sha256",
        "purpose",
        "release_sha",
        "run_id",
        "schema_version",
        "season_number",
        "status",
    }
)
_ROOT_RECEIPT_KEYS = _ROOT_INTENT_KEYS | {
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


def _canonical(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_sha(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


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
            or stat.S_IMODE(metadata.st_mode) != mode
            or (metadata.st_uid, metadata.st_gid) != owner
            or path.resolve(strict=True) != path
        ):
            raise OSError
    except OSError as error:
        raise NextAdjudicationSubmissionError("adjudication evidence unavailable") from error


def _stable(
    path: Path,
    *,
    maximum: int,
    mode: int,
    owner: tuple[int, int],
) -> bytes:
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
        if (
            _identity(before) != _identity(opened)
            or _identity(opened) != _identity(after)
            or len(raw) != opened.st_size
        ):
            raise OSError
        return raw
    except OSError as error:
        raise NextAdjudicationSubmissionError("adjudication evidence unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _decode(raw: bytes) -> dict[str, object]:
    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in values:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise NextAdjudicationSubmissionError("adjudication evidence invalid") from error
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise NextAdjudicationSubmissionError("adjudication evidence invalid")
    return value


def _record(path: Path) -> tuple[dict[str, object], str]:
    raw = _stable(path, maximum=MAX_RECORD_BYTES, mode=0o600, owner=(0, 0))
    return _decode(raw), _sha(raw)


def _read_request(stream: BinaryIO) -> dict[str, object]:
    raw = stream.readline(contract.REQUEST_MAX_BYTES + 1)
    if stream.read(1):
        raise NextAdjudicationSubmissionError("invalid adjudication request")
    try:
        return contract.parse_request(raw)
    except (TypeError, ValueError) as error:
        raise NextAdjudicationSubmissionError("invalid adjudication request") from error


def _validate_authorization(request: Mapping[str, object]) -> str:
    _directory(AUTHORIZATION_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    path = AUTHORIZATION_ROOT / f"{request['authorization_id']}.json"
    raw = _stable(
        path,
        maximum=contract.REQUEST_MAX_BYTES,
        mode=0o600,
        owner=(ROOT_UID, ROOT_GID),
    )
    try:
        if contract.parse_request(raw) != dict(request):
            raise ValueError
    except (TypeError, ValueError) as error:
        raise NextAdjudicationSubmissionError("adjudication authorization invalid") from error
    return _sha(raw)


def _run_directory(request: Mapping[str, object]) -> Path:
    _directory(RUNS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    object_root = RUNS_ROOT / f"sha256-{request['archive_sha256']}"
    runs = object_root / "review-runs"
    _directory(object_root, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    _directory(runs, mode=0o700, owner=(WORKER_UID, WORKER_GID))
    candidate = runs / str(request["run_id"])
    try:
        metadata = candidate.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or candidate.resolve(strict=True) != candidate
            or candidate.resolve(strict=False).parent != runs.resolve(strict=True)
        ):
            raise OSError
    except OSError as error:
        raise NextAdjudicationSubmissionError("adjudication run invalid") from error
    _directory(candidate, mode=0o700, owner=(WORKER_UID, WORKER_GID))
    return candidate


def _classes(
    contents: Mapping[str, bytes],
) -> tuple[dict[str, bytes], dict[str, bytes], dict[str, bytes], dict[str, bytes]]:
    artifacts: dict[str, bytes] = {}
    journals: dict[str, bytes] = {}
    outputs: dict[str, bytes] = {}
    derived: dict[str, bytes] = {}
    for name, raw in contents.items():
        if name == STATE_NAME:
            continue
        if name.startswith("."):
            journals[name] = raw
        elif name.endswith("-output.jsonl") or name.endswith("-api-errors.jsonl"):
            outputs[name] = raw
        elif name in {"candidates.jsonl", "source-manifest.json"} or (
            name.startswith("primary-part-") and name.endswith("-requests.jsonl")
        ):
            artifacts[name] = raw
        else:
            derived[name] = raw
    return artifacts, journals, outputs, derived


def _set_digest(contents: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(contents):
        encoded = name.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(contents[name]).to_bytes(8, "big"))
        digest.update(contents[name])
    return digest.hexdigest()


def _inventory(
    run: Path,
) -> tuple[
    dict[str, bytes],
    dict[str, bytes],
    dict[str, bytes],
    dict[str, bytes],
    dict[str, bytes],
    SpeakerReviewRunState,
]:
    try:
        files, _, _, _, extra = phase69._inventory(run)
        state_payload = extra["state"]
        canonical, state = load_validated_run_state(
            run, DEFAULT_SPEAKER_REVIEW_CONFIGURATION
        )
        if (
            canonical != run
            or state_payload != state.to_dict()
            or state.run_id != run.name
        ):
            raise ValueError
        required, optional = worker._expected_names(state)
        if not required <= set(files) or not set(files) <= required | optional:
            raise ValueError
        artifacts, journals, outputs, derived = _classes(files)
        total = sum(len(raw) for raw in files.values())
        if total > MAX_TOTAL_BYTES:
            raise ValueError
        return files, artifacts, journals, outputs, derived, state
    except NextAdjudicationSubmissionError:
        raise
    except Exception as error:
        raise NextAdjudicationSubmissionError("adjudication inventory invalid") from error


def _cost_micros(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NextAdjudicationSubmissionError("adjudication cost invalid")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise NextAdjudicationSubmissionError("adjudication cost invalid") from error
    if not decimal.is_finite() or decimal < 0:
        raise NextAdjudicationSubmissionError("adjudication cost invalid")
    value_micros = int((decimal * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
    if value_micros > contract.MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise NextAdjudicationSubmissionError("adjudication cost invalid")
    return value_micros


def _phase70_request(
    request: Mapping[str, object], intent: Mapping[str, object]
) -> tuple[dict[str, object], bytes, str]:
    authorization_id = intent.get("authorization_id")
    maximum = intent.get("maximum_authorized_cost_microusd")
    if not isinstance(authorization_id, str) or type(maximum) is not int:
        raise ValueError
    raw = _stable(
        AUTHORIZATION_ROOT / f"{authorization_id}.json",
        maximum=phase70_contract.REQUEST_MAX_BYTES,
        mode=0o600,
        owner=(ROOT_UID, ROOT_GID),
    )
    phase_request = phase70_contract.parse_request(raw)
    expected = {
        "archive_sha256": request["archive_sha256"],
        "authorization_id": authorization_id,
        "maximum_authorized_cost_microusd": maximum,
        "operation": phase70_contract.OPERATION,
        "purpose": phase70_contract.PURPOSE,
        "run_id": request["run_id"],
        "schema_version": phase70_contract.PROTOCOL_VERSION,
        "season_number": phase70_contract.SEASON_NUMBER,
    }
    if phase_request != expected:
        raise ValueError
    return phase_request, raw, _sha(raw)


def _validate_phase70_predecessor(
    request: Mapping[str, object],
    run: Path,
    contents: Mapping[str, bytes],
    state: SpeakerReviewRunState,
) -> tuple[dict[str, object], str, str, str, int, int]:
    """Validate Phase 70's intent, receipt, and earlier chain exactly."""

    try:
        _directory(PHASE70_RECEIPTS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
        intent, intent_sha = _record(PHASE70_RECEIPTS_ROOT / f"{request['run_id']}.intent.json")
        receipt, receipt_sha = _record(PHASE70_RECEIPTS_ROOT / f"{request['run_id']}.json")
        if set(intent) != phase70._BINDING_KEYS or set(receipt) != phase70._RECEIPT_KEYS:
            raise ValueError
        phase_request, phase_auth_raw, phase_auth_sha = _phase70_request(request, intent)
        validated_intent = phase70._validate_intent(
            intent,
            request=phase_request,
            authorization_sha256=phase_auth_sha,
        )
        phase70._validate_predecessors_from_intent(validated_intent, phase_request)
        if any(
            receipt.get(key) != value
            for key, value in intent.items()
            if key != "status"
        ) or receipt.get("status") != "receipt":
            raise ValueError
        result = phase70_contract.validate_aggregate(receipt.get("result"), status="observed")
        if (
            result["run_id"] != state.run_id
            or result["adjudication_part_count"] != state.adjudication_part_count
            or result["adjudication_completed_part_count"] != 1
            or result["run_status"] != SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED.value
            or _cost_micros(state.actual_primary_cost_usd)
            != result["actual_primary_cost_microusd"]
            or result["estimated_adjudication_cost_microusd"]
            != intent["estimated_adjudication_cost_microusd"]
        ):
            raise ValueError
        if (
            state.status is SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED
            and state.adjudication_completed_part_count == 1
        ):
            # The root intent is written before the application can create
            # part-two journals.  On a retry, compare Phase 70's post receipt
            # with the reconstructed clean checkpoint, then validate any
            # active journals independently in the worker path.
            clean_contents = _fresh_binding_contents(contents, state)
            artifacts, journals, outputs, derived = _classes(clean_contents)
            if (
                receipt.get("post_run_state_sha256") != _sha(clean_contents[STATE_NAME])
                or receipt.get("post_artifact_set_sha256") != _set_digest(artifacts)
                or receipt.get("post_journal_set_sha256") != _set_digest(journals)
                or receipt.get("post_output_set_sha256") != _set_digest(outputs)
                or receipt.get("post_derived_set_sha256") != _set_digest(derived)
            ):
                raise ValueError
        preparation_value, preparation_sha = preparation._validate_preparation(request)
        estimate = result["estimated_adjudication_cost_microusd"]
        actual = result["actual_primary_cost_microusd"]
        if type(estimate) is not int or type(actual) is not int:
            raise ValueError
        # Keep the auth bytes in the validation path so a later caller cannot
        # substitute a same-shaped Phase 70 record under another identity.
        if _sha(phase_auth_raw) != intent["authorization_sha256"]:
            raise ValueError
        return preparation_value, preparation_sha, intent_sha, receipt_sha, estimate, actual
    except NextAdjudicationSubmissionError:
        raise
    except Exception as error:
        raise NextAdjudicationSubmissionError("phase 70 predecessor invalid") from error


def _pre_submission_snapshot(
    contents: Mapping[str, bytes], state: SpeakerReviewRunState, intent: Mapping[str, object]
) -> tuple[dict[str, bytes], SpeakerReviewRunState]:
    """Reconstruct the exact completed checkpoint bound by a root intent."""

    try:
        completed = state.adjudication_completed_part_count
        if (
            state.status is not SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED
            or len(state.adjudication_batch_ids) != completed + 1
            or len(state.adjudication_input_file_ids) != completed + 1
            or not isinstance(intent.get("pre_updated_at"), str)
        ):
            raise ValueError
        previous = replace(
            state,
            status=SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED,
            updated_at=intent["pre_updated_at"],
            adjudication_batch_id=state.adjudication_batch_ids[-2],
            adjudication_input_file_id=state.adjudication_input_file_ids[-2],
            adjudication_batch_ids=state.adjudication_batch_ids[:-1],
            adjudication_input_file_ids=state.adjudication_input_file_ids[:-1],
        )
        snapshot = dict(contents)
        snapshot.pop(_INTENT_NAME.format(part=completed + 1), None)
        snapshot.pop(_COMPLETED_NAME.format(part=completed + 1), None)
        snapshot[STATE_NAME] = _canonical(previous.to_dict())
        return snapshot, previous
    except (IndexError, KeyError, TypeError, ValueError) as error:
        raise NextAdjudicationSubmissionError("adjudication replay invalid") from error


def _receipt_paths(run_id: str, part: int) -> tuple[Path, Path]:
    if (
        not isinstance(run_id, str)
        or not run_id
        or type(part) is not int
        or part <= 0
    ):
        raise NextAdjudicationSubmissionError("adjudication receipt path invalid")
    # Phase 70 authenticates the first completed part.  This host command is
    # intentionally limited to target part two, so one run-scoped pair is
    # sufficient.  A future host boundary for part k must use part-scoped
    # receipts tied to that part's observation predecessor.
    if part != 2:
        raise NextAdjudicationSubmissionError("adjudication receipt path invalid")
    return (
        RECEIPTS_ROOT / f"{run_id}.intent.json",
        RECEIPTS_ROOT / f"{run_id}.json",
    )


def _validate_state_shape(state: SpeakerReviewRunState) -> None:
    try:
        worker._validate_checkpoint_shape(
            state,
            submitted=state.status is SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED,
        )
    except Exception as error:
        raise NextAdjudicationSubmissionError("adjudication checkpoint invalid") from error
    if state.adjudication_part_count <= 1:
        raise NextAdjudicationSubmissionError("no adjudication part remains")
    # The Phase 70 predecessor is an authenticated observation of part one;
    # this root boundary therefore authorizes only the next (part-two) call.
    # The workflow/worker remain generic for later part transitions.
    if state.adjudication_completed_part_count != 1:
        raise NextAdjudicationSubmissionError("adjudication predecessor invalid")
    if state.status not in {
        SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED,
        SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED,
    }:
        raise NextAdjudicationSubmissionError("adjudication checkpoint invalid")


def _validate_completed_evidence(
    run: Path,
    contents: Mapping[str, bytes],
    state: SpeakerReviewRunState,
) -> None:
    try:
        worker._validate_completed_parts(
            run, contents, state, state.adjudication_completed_part_count
        )
    except Exception as error:
        raise NextAdjudicationSubmissionError("adjudication evidence invalid") from error


def _estimate_cost(contents: Mapping[str, bytes], state: SpeakerReviewRunState) -> int:
    try:
        requests = worker._parse_requests(contents, state)
        return _cost_micros(
            worker.estimate_batch_cost_usd(
                requests=requests,
                model=worker.DEFAULT_MODEL_CONFIGURATION.speaker_adjudication_model,
                configuration=worker.DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
            )
        )
    except NextAdjudicationSubmissionError:
        raise
    except Exception as error:
        raise NextAdjudicationSubmissionError("adjudication cost invalid") from error


def _binding(
    request: Mapping[str, object],
    *,
    authorization_sha256: str,
    preparation_value: Mapping[str, object],
    preparation_sha256: str,
    phase70_intent_sha256: str,
    phase70_receipt_sha256: str,
    estimated: int,
    contents: Mapping[str, bytes],
    state: SpeakerReviewRunState,
) -> dict[str, object]:
    artifacts, journals, outputs, derived = _classes(contents)
    next_part = state.adjudication_completed_part_count + 1
    next_name = _OUTPUT_NAME.format(part=next_part).replace("-output", "-requests")
    if next_name not in contents:
        raise NextAdjudicationSubmissionError("adjudication request unavailable")
    return {
        "actual_primary_cost_microusd": _cost_micros(state.actual_primary_cost_usd),
        "adjudication_completed_part_count": state.adjudication_completed_part_count,
        "adjudication_part_count": state.adjudication_part_count,
        "archive_sha256": request["archive_sha256"],
        "authorization_id": request["authorization_id"],
        "authorization_sha256": authorization_sha256,
        "configuration_sha256": preparation_value["config_sha"],
        "estimated_adjudication_cost_microusd": estimated,
        "image_reference": preparation_value["image"],
        "maximum_authorized_cost_microusd": request["maximum_authorized_cost_microusd"],
        "next_adjudication_part_number": next_part,
        "next_request_sha256": _sha(contents[next_name]),
        "operation": contract.OPERATION,
        "phase70_observation_intent_sha256": phase70_intent_sha256,
        "phase70_observation_receipt_sha256": phase70_receipt_sha256,
        "pre_artifact_set_sha256": _set_digest(artifacts),
        "pre_derived_set_sha256": _set_digest(derived),
        "pre_journal_set_sha256": _set_digest(journals),
        "pre_output_set_sha256": _set_digest(outputs),
        "pre_run_state_sha256": _sha(contents[STATE_NAME]),
        "pre_state_binding_sha256": _state_binding_sha256(state),
        "pre_updated_at": state.updated_at,
        "prep_receipt_sha256": preparation_sha256,
        "purpose": contract.PURPOSE,
        "release_sha": preparation_value["release_sha"],
        "run_id": request["run_id"],
        "schema_version": contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
        "status": "intent",
    }


def _state_binding_sha256(state: SpeakerReviewRunState) -> str:
    return _sha(
        _canonical(
            {
                key: value
                for key, value in state.to_dict().items()
                if key not in ROOT_STATE_MUTABLE
            }
        )
    )


def _validate_binding(value: object, request: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _ROOT_INTENT_KEYS:
        raise NextAdjudicationSubmissionError("adjudication intent invalid")
    if (
        value.get("status") != "intent"
        or value.get("operation") != contract.OPERATION
        or value.get("purpose") != contract.PURPOSE
        or value.get("archive_sha256") != request["archive_sha256"]
        or value.get("authorization_id") != request["authorization_id"]
        or value.get("run_id") != request["run_id"]
        or value.get("schema_version") != contract.PROTOCOL_VERSION
        or value.get("season_number") != contract.SEASON_NUMBER
        or value.get("maximum_authorized_cost_microusd")
        != request["maximum_authorized_cost_microusd"]
        or type(value.get("actual_primary_cost_microusd")) is not int
        or type(value.get("estimated_adjudication_cost_microusd")) is not int
        or type(value.get("adjudication_part_count")) is not int
        or type(value.get("adjudication_completed_part_count")) is not int
        or type(value.get("next_adjudication_part_number")) is not int
        or value["next_adjudication_part_number"]
        != value["adjudication_completed_part_count"] + 1
        or not isinstance(value.get("pre_updated_at"), str)
        or not value["pre_updated_at"].strip()
    ):
        raise NextAdjudicationSubmissionError("adjudication intent invalid")
    if not (
        0 < value["adjudication_completed_part_count"] < value["adjudication_part_count"]
        and value["actual_primary_cost_microusd"] >= 0
        and value["estimated_adjudication_cost_microusd"] >= 0
    ):
        raise NextAdjudicationSubmissionError("adjudication intent invalid")
    for name in (
        "authorization_sha256",
        "configuration_sha256",
        "next_request_sha256",
        "phase70_observation_intent_sha256",
        "phase70_observation_receipt_sha256",
        "pre_artifact_set_sha256",
        "pre_derived_set_sha256",
        "pre_journal_set_sha256",
        "pre_output_set_sha256",
        "pre_run_state_sha256",
        "pre_state_binding_sha256",
        "prep_receipt_sha256",
    ):
        if not _is_sha(value.get(name)):
            raise NextAdjudicationSubmissionError("adjudication intent invalid")
    return dict(value)


def _write_once(path: Path, value: Mapping[str, object]) -> None:
    _directory(RECEIPTS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    raw = _canonical(value)
    if len(raw) > MAX_RECORD_BYTES:
        raise NextAdjudicationSubmissionError("adjudication receipt unavailable")
    if os.path.lexists(path):
        existing, _ = _record(path)
        if existing != dict(value):
            raise NextAdjudicationSubmissionError("adjudication receipt conflict")
        return
    pending = path.with_name(f".{path.name}.pending")
    descriptor = -1
    try:
        if os.path.lexists(pending):
            if _stable(pending, maximum=MAX_RECORD_BYTES, mode=0o600, owner=(0, 0)) != raw:
                raise OSError
        else:
            descriptor = os.open(
                pending,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            phase69._fsync_directory(RECEIPTS_ROOT)
        os.link(pending, path, follow_symlinks=False)
        phase69._fsync_directory(RECEIPTS_ROOT)
        pending.unlink()
        phase69._fsync_directory(RECEIPTS_ROOT)
    except FileExistsError:
        existing, _ = _record(path)
        if existing != dict(value):
            raise NextAdjudicationSubmissionError("adjudication receipt conflict") from None
        pending.unlink(missing_ok=True)
    except OSError as error:
        raise NextAdjudicationSubmissionError("adjudication receipt unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _safe_env() -> dict[str, str]:
    return {
        "PATH": "/usr/sbin:/usr/bin",
        "HOME": "/root",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _worker_args(
    request: Mapping[str, object], run_parent: Path, bindings: Mapping[str, str]
) -> list[str]:
    environment = {
        contract.ENV_ARCHIVE_SHA256: str(request["archive_sha256"]),
        contract.ENV_RUN_ID: str(request["run_id"]),
        contract.ENV_AUTHORIZATION_ID: str(request["authorization_id"]),
        contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: str(
            request["maximum_authorized_cost_microusd"]
        ),
        **dict(bindings),
    }
    command = [
        "docker",
        "compose",
        "--progress",
        "quiet",
        "--env-file",
        os.fspath(ENV_FILE),
        "--profile",
        host.REVIEW_NEXT_ADJUDICATION_COMPOSE_PROFILE,
        "-f",
        os.fspath(COMPOSE_PATH),
        "run",
        "--rm",
        "--no-deps",
        "--no-TTY",
        "--pull",
        "never",
        "--name",
        host.REVIEW_NEXT_ADJUDICATION_CONTAINER_NAME,
        "--user",
        f"{WORKER_UID}:{WORKER_GID}",
    ]
    for name, value in sorted(environment.items()):
        command.extend(("--env", f"{name}={value}"))
    command.extend(
        (
            "--volume",
            f"{run_parent.as_posix()}:{host.REVIEW_NEXT_ADJUDICATION_RUNS_TARGET}:rw",
            host.REVIEW_NEXT_ADJUDICATION_COMPOSE_SERVICE,
        )
    )
    return command


def _container_identity_is_exact(runs: Path) -> bool:
    """Return true only for this operation's fixed-name Compose container."""

    try:
        expected = subprocess.run(
            [
                "docker",
                "compose",
                "--progress",
                "quiet",
                "--env-file",
                os.fspath(ENV_FILE),
                "--profile",
                host.REVIEW_NEXT_ADJUDICATION_COMPOSE_PROFILE,
                "-f",
                os.fspath(COMPOSE_PATH),
                "config",
                "--images",
                host.REVIEW_NEXT_ADJUDICATION_COMPOSE_SERVICE,
            ],
            cwd=RELEASE_ROOT,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=host.REVIEW_NEXT_ADJUDICATION_KILL_AFTER_SECONDS,
        )
        image = expected.stdout.decode("utf-8").strip()
        inspected = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .}}",
                host.REVIEW_NEXT_ADJUDICATION_CONTAINER_NAME,
            ],
            cwd=RELEASE_ROOT,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=host.REVIEW_NEXT_ADJUDICATION_KILL_AFTER_SECONDS,
        )
        value = json.loads(inspected.stdout.decode("utf-8"))
        config = value.get("Config", {})
        host_config = value.get("HostConfig", {})
        settings = value.get("NetworkSettings", {})
        labels = config.get("Labels", {})
        networks = settings.get("Networks", {})
        mounts = value.get("Mounts", [])
        if not isinstance(mounts, list):
            return False
        if (
            inspected.returncode != 0
            or expected.returncode != 0
            or not image
            or value.get("Name") != f"/{host.REVIEW_NEXT_ADJUDICATION_CONTAINER_NAME}"
            or config.get("Image") != image
            or config.get("User") != f"{WORKER_UID}:{WORKER_GID}"
            or config.get("WorkingDir") != host.REVIEW_NEXT_ADJUDICATION_CONTAINER_WORKDIR
            or config.get("Cmd") != list(host.REVIEW_NEXT_ADJUDICATION_CONTAINER_COMMAND)
            or labels.get("com.docker.compose.service")
            != host.REVIEW_NEXT_ADJUDICATION_COMPOSE_SERVICE
            or labels.get("com.docker.compose.oneoff") != "True"
            or labels.get("com.docker.compose.project")
            != host.REVIEW_NEXT_ADJUDICATION_COMPOSE_PROJECT
            or labels.get("com.docker.compose.project.config_files") != os.fspath(COMPOSE_PATH)
            or labels.get("com.docker.compose.project.working_dir") != os.fspath(RELEASE_ROOT)
            or host_config.get("ReadonlyRootfs") is not True
            or host_config.get("Privileged") is not False
            or host_config.get("CapDrop") != ["ALL"]
            or "no-new-privileges:true" not in (host_config.get("SecurityOpt") or [])
            or host_config.get("PidsLimit") != 128
            or not isinstance(networks, dict)
            or set(networks) != {host.REVIEW_NEXT_ADJUDICATION_NETWORK}
        ):
            return False
        destinations: dict[str, tuple[str, bool]] = {}
        for mount in mounts:
            if not isinstance(mount, dict):
                return False
            destination = mount.get("Destination")
            source = mount.get("Source")
            writable = mount.get("RW")
            if (
                not isinstance(destination, str)
                or not isinstance(source, str)
                or type(writable) is not bool
                or destination in destinations
            ):
                return False
            destinations[destination] = (source, writable)
        return (
            destinations.get(host.REVIEW_NEXT_ADJUDICATION_RUNS_TARGET.as_posix())
            == (runs.as_posix(), True)
            and destinations.get(host.REVIEW_NEXT_ADJUDICATION_SECRET_TARGET, ("", True))[1]
            is False
            and destinations.get(host.REVIEW_NEXT_ADJUDICATION_TMP_TARGET) == ("", True)
            and set(destinations)
            == {
                host.REVIEW_NEXT_ADJUDICATION_RUNS_TARGET.as_posix(),
                host.REVIEW_NEXT_ADJUDICATION_SECRET_TARGET,
                host.REVIEW_NEXT_ADJUDICATION_TMP_TARGET,
            }
        )
    except (
        OSError,
        subprocess.SubprocessError,
        UnicodeError,
        json.JSONDecodeError,
        AttributeError,
        TypeError,
        ValueError,
    ):
        return False


def _cleanup_worker(runs: Path) -> None:
    if not _container_identity_is_exact(runs):
        return
    try:
        subprocess.run(
            ["docker", "rm", "--force", host.REVIEW_NEXT_ADJUDICATION_CONTAINER_NAME],
            cwd=RELEASE_ROOT,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=host.REVIEW_NEXT_ADJUDICATION_KILL_AFTER_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return


def _terminate_worker(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=host.REVIEW_NEXT_ADJUDICATION_KILL_AFTER_SECONDS)
            return
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except OSError:
            pass
    process.wait()


def _read_bounded(stream: BinaryIO) -> bytes:
    try:
        return stream.read(contract.OUTPUT_MAX_BYTES + 1)
    finally:
        stream.close()


def _run_worker(
    request: Mapping[str, object], run_parent: Path, bindings: Mapping[str, str]
) -> dict[str, object]:
    process: subprocess.Popen[bytes] | None = None
    try:
        _cleanup_worker(run_parent)
        process = subprocess.Popen(
            _worker_args(request, run_parent, bindings),
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
            output = pool.submit(_read_bounded, process.stdout)
            errors = pool.submit(_read_bounded, process.stderr)
            try:
                code = process.wait(
                    timeout=host.REVIEW_NEXT_ADJUDICATION_TIMEOUT_SECONDS - 60
                )
            except subprocess.TimeoutExpired as error:
                _terminate_worker(process)
                output.result(timeout=5)
                errors.result(timeout=5)
                raise NextAdjudicationSubmissionError("adjudication worker timeout") from error
            stdout, stderr = output.result(timeout=5), errors.result(timeout=5)
        if code != 0 or stderr or len(stdout) > contract.OUTPUT_MAX_BYTES:
            raise OSError
        try:
            return contract.parse_aggregate(stdout)
        except (TypeError, ValueError) as error:
            raise NextAdjudicationSubmissionError("adjudication aggregate invalid") from error
    except NextAdjudicationSubmissionError:
        raise
    except (OSError, subprocess.SubprocessError, TimeoutError) as error:
        raise NextAdjudicationSubmissionError("adjudication worker failed") from error
    finally:
        if process is not None and process.poll() is None:
            try:
                _terminate_worker(process)
            except (OSError, subprocess.SubprocessError):
                pass
        _cleanup_worker(run_parent)


def _checkpoint_bindings(
    contents: Mapping[str, bytes],
    artifacts: Mapping[str, bytes],
    journals: Mapping[str, bytes],
    outputs: Mapping[str, bytes],
    derived: Mapping[str, bytes],
    state: SpeakerReviewRunState,
) -> dict[str, str]:
    next_part = state.adjudication_completed_part_count + 1
    request_name = f"adjudication-part-{next_part:04d}-requests.jsonl"
    return {
        contract.ENV_EXPECTED_REQUEST_SHA256: _sha(contents[request_name]),
        contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: _sha(contents[STATE_NAME]),
        contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: _set_digest(artifacts),
        contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: _set_digest(journals),
        contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256: _set_digest(outputs),
        contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256: _set_digest(derived),
    }


def _fresh_binding_contents(
    contents: Mapping[str, bytes], state: SpeakerReviewRunState
) -> dict[str, bytes]:
    """Return the pre-provider snapshot represented by a fresh root intent."""

    snapshot = dict(contents)
    part = state.adjudication_completed_part_count + 1
    snapshot.pop(_INTENT_NAME.format(part=part), None)
    snapshot.pop(_COMPLETED_NAME.format(part=part), None)
    return snapshot


def _validate_worker_result(
    value: object,
    *,
    request: Mapping[str, object],
    before: SpeakerReviewRunState,
    status: str,
    estimated: int,
    actual: int,
) -> dict[str, object]:
    try:
        result = contract.validate_aggregate(value, status=status)
    except (TypeError, ValueError) as error:
        raise NextAdjudicationSubmissionError("adjudication worker result invalid") from error
    expected_run_status = (
        SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED.value
        if status in {"submitted", "already_submitted"}
        else SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED.value
    )
    if (
        result["run_id"] != request["run_id"]
        or result["operation"] != contract.OPERATION
        or result["purpose"] != contract.PURPOSE
        or result["season_number"] != contract.SEASON_NUMBER
        or result["run_status"] != expected_run_status
        or result["adjudication_part_count"] != before.adjudication_part_count
        or result["adjudication_completed_part_count"]
        != before.adjudication_completed_part_count
        or result["estimated_adjudication_cost_microusd"] != estimated
        or result["actual_primary_cost_microusd"] != actual
        or result["submitted_part_count"] != (0 if status == "reconciliation_required" else 1)
    ):
        raise NextAdjudicationSubmissionError("adjudication worker result invalid")
    return result


def _state_transition(
    before: SpeakerReviewRunState,
    after: SpeakerReviewRunState,
) -> None:
    before_payload = before.to_dict()
    after_payload = after.to_dict()
    if set(before_payload) != set(after_payload):
        raise NextAdjudicationSubmissionError("adjudication post-state invalid")
    if any(
        before_payload[key] != after_payload[key]
        for key in before_payload
        if key not in ROOT_STATE_MUTABLE
    ):
        raise NextAdjudicationSubmissionError("adjudication immutable state changed")
    if (
        before.status is not SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED
        or after.status is not SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED
        or after.adjudication_completed_part_count
        != before.adjudication_completed_part_count
        or len(after.adjudication_batch_ids)
        != len(before.adjudication_batch_ids) + 1
        or len(after.adjudication_input_file_ids)
        != len(before.adjudication_input_file_ids) + 1
        or after.adjudication_batch_ids[:-1] != before.adjudication_batch_ids
        or after.adjudication_input_file_ids[:-1] != before.adjudication_input_file_ids
        or after.adjudication_batch_id != after.adjudication_batch_ids[-1]
        or after.adjudication_input_file_id != after.adjudication_input_file_ids[-1]
    ):
        raise NextAdjudicationSubmissionError("adjudication post-state invalid")


def _post_validate(
    before_files: Mapping[str, bytes],
    after_files: Mapping[str, bytes],
    before: SpeakerReviewRunState,
    after: SpeakerReviewRunState,
    *,
    result_status: str,
) -> None:
    for name, raw in before_files.items():
        if name != STATE_NAME and (
            name not in after_files or _sha(after_files[name]) != _sha(raw)
        ):
            raise NextAdjudicationSubmissionError("adjudication immutable evidence changed")
    added = set(after_files) - set(before_files)
    part = before.adjudication_completed_part_count + 1
    allowed_journals = {
        _INTENT_NAME.format(part=part),
        _COMPLETED_NAME.format(part=part),
    }
    if result_status in {"submitted", "already_submitted"}:
        if added - allowed_journals:
            raise NextAdjudicationSubmissionError("adjudication post-inventory invalid")
        if any(
            name in after_files
            for name in (_OUTPUT_NAME.format(part=part), _API_ERRORS_NAME.format(part=part))
        ):
            raise NextAdjudicationSubmissionError("adjudication output invalid")
        if result_status == "submitted":
            _state_transition(before, after)
        elif before != after:
            raise NextAdjudicationSubmissionError("adjudication replay changed state")
    elif result_status == "reconciliation_required":
        if added or before_files != after_files or before != after:
            raise NextAdjudicationSubmissionError("adjudication reconciliation changed state")
    else:
        raise NextAdjudicationSubmissionError("adjudication worker result invalid")


def _receipt_payload(
    intent: Mapping[str, object],
    result: Mapping[str, object],
    contents: Mapping[str, bytes],
) -> dict[str, object]:
    artifacts, journals, outputs, derived = _classes(contents)
    return {
        **intent,
        "post_artifact_file_count": len(artifacts),
        "post_artifact_set_sha256": _set_digest(artifacts),
        "post_derived_file_count": len(derived),
        "post_derived_set_sha256": _set_digest(derived),
        "post_journal_file_count": len(journals),
        "post_journal_set_sha256": _set_digest(journals),
        "post_output_file_count": len(outputs),
        "post_output_set_sha256": _set_digest(outputs),
        "post_run_state_sha256": _sha(contents[STATE_NAME]),
        "result": dict(result),
        "status": "receipt",
    }


def _validate_receipt(
    value: object,
    *,
    intent: Mapping[str, object],
    contents: Mapping[str, bytes],
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _ROOT_RECEIPT_KEYS:
        raise NextAdjudicationSubmissionError("adjudication receipt invalid")
    if any(
        value.get(key) != expected
        for key, expected in intent.items()
        if key != "status"
    ) or value.get("status") != "receipt":
        raise NextAdjudicationSubmissionError("adjudication receipt invalid")
    expected = _receipt_payload(intent, value.get("result", {}), contents)
    if value != expected:
        raise NextAdjudicationSubmissionError("adjudication receipt invalid")
    try:
        return contract.validate_aggregate(value["result"])
    except (TypeError, ValueError) as error:
        raise NextAdjudicationSubmissionError("adjudication receipt invalid") from error


def _aggregate(
    request: Mapping[str, object],
    state: SpeakerReviewRunState,
    *,
    status: str,
    estimated: int,
    submitted: int,
) -> dict[str, object]:
    try:
        return contract.validate_aggregate(
            {
                "actual_primary_cost_microusd": _cost_micros(state.actual_primary_cost_usd),
                "adjudication_completed_part_count": state.adjudication_completed_part_count,
                "adjudication_part_count": state.adjudication_part_count,
                "estimated_adjudication_cost_microusd": estimated,
                "operation": contract.OPERATION,
                "purpose": contract.PURPOSE,
                "run_id": request["run_id"],
                "run_status": state.status.value,
                "season_number": contract.SEASON_NUMBER,
                "status": status,
                "submitted_part_count": submitted,
            },
            status=status,
        )
    except (TypeError, ValueError) as error:
        raise NextAdjudicationSubmissionError("adjudication aggregate invalid") from error


def process_request(request: Mapping[str, object]) -> dict[str, object]:
    try:
        request = contract.validate_request(request)
        authorization_sha = _validate_authorization(request)
        run = _run_directory(request)
        contents, artifacts, journals, outputs, derived, state = _inventory(run)
        _validate_state_shape(state)
        _validate_completed_evidence(run, contents, state)
        preparation_value, preparation_sha, phase70_intent_sha, phase70_receipt_sha, prior_estimate, prior_actual = (
            _validate_phase70_predecessor(request, run, contents, state)
        )
        if _cost_micros(state.actual_primary_cost_usd) != prior_actual:
            raise NextAdjudicationSubmissionError("adjudication predecessor cost changed")
        estimated = _estimate_cost(contents, state)
        if estimated != prior_estimate:
            raise NextAdjudicationSubmissionError("adjudication estimate changed")
        maximum = int(request["maximum_authorized_cost_microusd"])
        configured = _cost_micros(DEFAULT_SPEAKER_REVIEW_CONFIGURATION.maximum_run_cost_usd)
        if prior_actual + prior_estimate > maximum or prior_actual + prior_estimate > configured:
            raise NextAdjudicationSubmissionError("adjudication cost exceeds authorization")
        intent_path, receipt_path = _receipt_paths(
            str(request["run_id"]), state.adjudication_completed_part_count + 1
        )
        intent_exists = os.path.lexists(intent_path)
        receipt_exists = os.path.lexists(receipt_path)
        if receipt_exists and not intent_exists:
            raise NextAdjudicationSubmissionError("orphan adjudication receipt")

        # A submitted checkpoint is replay-only.  Its application journals and
        # request binding are checked by the worker's provider-disabled path;
        # this coordinator merely repairs a missing root receipt.
        if state.status is SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED:
            if not intent_exists:
                raise NextAdjudicationSubmissionError("adjudication intent missing")
            intent, _ = _record(intent_path)
            _validate_binding(intent, request)
            pre_contents, pre_state = _pre_submission_snapshot(contents, state, intent)
            recomputed = _binding(
                request,
                authorization_sha256=authorization_sha,
                preparation_value=preparation_value,
                preparation_sha256=preparation_sha,
                phase70_intent_sha256=phase70_intent_sha,
                phase70_receipt_sha256=phase70_receipt_sha,
                estimated=estimated,
                contents=pre_contents,
                state=pre_state,
            )
            if intent != recomputed:
                raise NextAdjudicationSubmissionError("adjudication intent binding changed")
            try:
                worker._validate_replay_evidence(run, contents, state)
            except Exception as error:
                raise NextAdjudicationSubmissionError("adjudication replay invalid") from error
            if preparation._active_binding() != (
                preparation_value["release_sha"],
                preparation_value["image"],
                preparation_value["config_sha"],
            ):
                raise NextAdjudicationSubmissionError("active runtime changed")
            if receipt_exists:
                receipt, _ = _record(receipt_path)
                result = _validate_receipt(receipt, intent=intent, contents=contents)
                if (
                    result["run_id"] != request["run_id"]
                    or result["estimated_adjudication_cost_microusd"] != estimated
                    or result["actual_primary_cost_microusd"] != prior_actual
                ):
                    raise NextAdjudicationSubmissionError("adjudication receipt invalid")
                return _aggregate(
                    request,
                    state,
                    status="already_submitted",
                    estimated=estimated,
                    submitted=1,
                )
            current_bindings = _checkpoint_bindings(
                contents, artifacts, journals, outputs, derived, state
            )
            worker_result = _run_worker(request, run.parent, current_bindings)
            _validate_worker_result(
                worker_result,
                request=request,
                before=state,
                status="already_submitted",
                estimated=estimated,
                actual=prior_actual,
            )
            after, after_artifacts, after_journals, after_outputs, after_derived, after_state = _inventory(run)
            _post_validate(
                contents,
                after,
                state,
                after_state,
                result_status="already_submitted",
            )
            try:
                worker._validate_replay_evidence(run, after, after_state)
            except Exception as error:
                raise NextAdjudicationSubmissionError("adjudication replay invalid") from error
            result = _aggregate(
                request,
                after_state,
                status="already_submitted",
                estimated=estimated,
                submitted=1,
            )
            _write_once(receipt_path, _receipt_payload(intent, result, after))
            return result

        # A root intent is created before any provider-bound call.  Existing
        # intent-only state is allowed to reach the worker, which returns a
        # reconciliation aggregate without touching the provider or secret.
        active_part = state.adjudication_completed_part_count + 1
        if any(
            name in contents
            for name in (
                _OUTPUT_NAME.format(part=active_part),
                _API_ERRORS_NAME.format(part=active_part),
            )
        ):
            raise NextAdjudicationSubmissionError("adjudication output invalid")
        fresh_contents = _fresh_binding_contents(contents, state)
        binding = _binding(
            request,
            authorization_sha256=authorization_sha,
            preparation_value=preparation_value,
            preparation_sha256=preparation_sha,
            phase70_intent_sha256=phase70_intent_sha,
            phase70_receipt_sha256=phase70_receipt_sha,
            estimated=estimated,
            contents=fresh_contents,
            state=state,
        )
        if intent_exists:
            intent, _ = _record(intent_path)
            if intent != binding:
                raise NextAdjudicationSubmissionError("adjudication intent binding changed")
        else:
            if receipt_exists:
                raise NextAdjudicationSubmissionError("orphan adjudication receipt")
            _write_once(intent_path, binding)
            intent = binding
        if receipt_exists:
            raise NextAdjudicationSubmissionError("adjudication receipt state invalid")
        worker_result = _run_worker(
            request,
            run.parent,
            _checkpoint_bindings(contents, artifacts, journals, outputs, derived, state),
        )
        checked_status = worker_result.get("status")
        if checked_status not in {"submitted", "reconciliation_required"}:
            raise NextAdjudicationSubmissionError("adjudication worker result invalid")
        _validate_worker_result(
            worker_result,
            request=request,
            before=state,
            status=str(checked_status),
            estimated=estimated,
            actual=prior_actual,
        )
        after, after_artifacts, after_journals, after_outputs, after_derived, after_state = _inventory(run)
        _post_validate(
            contents,
            after,
            state,
            after_state,
            result_status=str(checked_status),
        )
        if checked_status == "reconciliation_required":
            return _aggregate(
                request,
                after_state,
                status="reconciliation_required",
                estimated=estimated,
                submitted=0,
            )
        try:
            worker._validate_replay_evidence(run, after, after_state)
        except Exception as error:
            raise NextAdjudicationSubmissionError("adjudication post-state invalid") from error
        result = _aggregate(
            request,
            after_state,
            status="submitted",
            estimated=estimated,
            submitted=1,
        )
        if preparation._active_binding() != (
            preparation_value["release_sha"],
            preparation_value["image"],
            preparation_value["config_sha"],
        ):
            raise NextAdjudicationSubmissionError("active runtime changed")
        _write_once(receipt_path, _receipt_payload(intent, result, after))
        return result
    except NextAdjudicationSubmissionError:
        raise
    except Exception as error:
        raise NextAdjudicationSubmissionError("adjudication evidence invalid") from error


def _require_root() -> None:
    if (
        os.name != "posix"
        or not hasattr(os, "geteuid")
        or os.geteuid() != ROOT_UID
        or os.environ.get("SUDO_USER") != host.REVIEW_USER
        or platform.system() != "Linux"
        or platform.machine() != "x86_64"
    ):
        raise NextAdjudicationSubmissionError("invalid adjudication caller")


def main() -> int:
    try:
        _require_root()
        result = process_request(_read_request(sys.stdin.buffer))
        sys.stdout.buffer.write(contract.canonical_json(result))
        return 0
    except Exception:
        sys.stderr.write("error=speaker_review_next_adjudication_rejected\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
