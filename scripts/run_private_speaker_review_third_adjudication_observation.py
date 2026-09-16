"""Root-only coordinator for observing exactly adjudication part three.

It authenticates the complete Phase 72/73 evidence chain, performs at most one
provider observation, and publishes a new receipt only after the narrow
filesystem transition has been verified.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import importlib
import json
import os
import platform
import signal
import stat
import subprocess
import sys
import types
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from typing import BinaryIO, Final, Mapping

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
_ISOLATED_ROOT = bool(sys.flags.no_site)
if os.fspath(_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(_ROOT))
if os.fspath(_SCRIPTS) not in sys.path:
    sys.path.insert(0, os.fspath(_SCRIPTS))
if os.fspath(_ROOT / "src") not in sys.path:
    sys.path.insert(0, os.fspath(_ROOT / "src"))

from scripts import (  # noqa: E402
    private_speaker_review_third_adjudication_observation_contract as contract,
)
from scripts import (  # noqa: E402
    private_speaker_review_third_adjudication_observation_host_contract as host,
)
from scripts import (  # noqa: E402
    private_speaker_review_third_adjudication_submission_contract as submit_contract,
)

try:  # Application/provider imports remain outside the privileged root boundary.
    if sys.flags.no_site:
        raise ModuleNotFoundError("isolated root")
    _config = importlib.import_module("cinegraph.config")
    _enum = importlib.import_module("cinegraph.domain.enums.enum")
    _workflow = importlib.import_module("cinegraph.ingestion.speaker_review.workflow")
    DEFAULT_SPEAKER_REVIEW_CONFIGURATION = _config.DEFAULT_SPEAKER_REVIEW_CONFIGURATION
    SpeakerReviewRunState = _workflow.SpeakerReviewRunState
    load_validated_run_state = _workflow.load_validated_run_state
    from scripts import (  # noqa: E402
        observe_third_private_speaker_review_adjudication_workspace as worker,
    )
    from scripts import (  # noqa: E402
        run_private_speaker_review_next_primary as preparation,
    )
    from scripts import (  # noqa: E402
        run_private_speaker_review_observation as observation,
    )
    from scripts import (  # noqa: E402
        run_private_speaker_review_third_adjudication as submit,
    )
except ModuleNotFoundError:  # pragma: no cover - exercised by isolated launch test
    # The host path is stdlib-only under ``-I -S``.  Phase 73 supplies a
    # complete fallback state/inventory implementation; only the worker module
    # (and its provider dependencies) stays outside this process.
    submit = importlib.import_module("scripts.run_private_speaker_review_third_adjudication")
    preparation = importlib.import_module("scripts.run_private_speaker_review_next_primary")
    observation = importlib.import_module("scripts.run_private_speaker_review_observation")
    SpeakerReviewRunState = submit.SpeakerReviewRunState
    load_validated_run_state = submit.load_validated_run_state
    DEFAULT_SPEAKER_REVIEW_CONFIGURATION = submit.DEFAULT_SPEAKER_REVIEW_CONFIGURATION

    def _isolated_expected_inventory_names(
        state: object, **_kwargs: object
    ) -> tuple[set[str], set[str]]:
        required, optional = submit._expected_inventory_names(state)
        next_part = contract.OBSERVED_PART_NUMBER + 1
        optional -= {
            f".adjudication-part-{next_part:04d}-submission-intent.json",
            f".adjudication-part-{next_part:04d}-submission-completed.json",
        }
        return required, optional

    worker = types.SimpleNamespace(
        _expected_inventory_names=_isolated_expected_inventory_names,
        _validate_checkpoint_shape=lambda state, *, submitted: submit._validate_state_shape(state),
        _validate_completed_parts=lambda run,
        contents,
        state,
        count: submit._validate_completed_evidence(run, contents, state),
        _validate_journal=lambda run, contents, state, part, **kwargs: submit._stdlib_journal(
            contents, state, part, **kwargs
        ),
    )


class ThirdAdjudicationObservationError(RuntimeError):
    """Generic rejection that never exposes private/provider details."""


RELEASE_ROOT: Final = _ROOT
RUNS_ROOT: Final = host.SPEAKER_REVIEW_RUNS_ROOT
AUTHORIZATION_ROOT: Final = host.REVIEW_AUTHORIZATION_ROOT
SUBMISSION_RECEIPTS_ROOT: Final = host.REVIEW_THIRD_ADJUDICATION_SUBMISSION_RECEIPTS_ROOT
OBSERVATION_RECEIPTS_ROOT: Final = host.REVIEW_THIRD_ADJUDICATION_OBSERVATION_RECEIPTS_ROOT
COMPOSE_PATH: Final = RELEASE_ROOT / "deploy/compose.yaml"
ENV_FILE: Final = host.ENV_FILE
WORKER_MOUNT: Final = "/review-workspace/review-runs"
WORKER_UID: Final = host.UID_IN_CONTAINER
WORKER_GID: Final = host.GID_IN_CONTAINER
WORKER_STATIC_ENVIRONMENT: Final = {
    "CINEGRAPH_ENVIRONMENT": "development",
    "CINEGRAPH_SPEAKER_REVIEW_RUNS_ROOT": "/review-workspace/review-runs",
    "FASTEMBED_CACHE_PATH": "/home/cinegraph/.cache/fastembed",
    "HF_HOME": "/home/cinegraph/.cache/huggingface",
    "HF_HUB_CACHE": "/home/cinegraph/.cache/huggingface/hub",
    "HF_HUB_DISABLE_PROGRESS_BARS": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_XET": "1",
    "HF_HUB_OFFLINE": "1",
    "HF_XET_CACHE": "/home/cinegraph/.cache/huggingface/xet",
    "MKL_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
}
FORBIDDEN_WORKER_ENVIRONMENT: Final = frozenset(
    {
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "OPENAI_API_KEY",
        "PYTHONHOME",
    }
)
ROOT_UID: Final = 0
ROOT_GID: Final = 0
MAX_RECORD_BYTES: Final = 128 * 1024
MAX_FILE_BYTES: Final = 64 * 1024 * 1024
MAX_TOTAL_BYTES: Final = 256 * 1024 * 1024
STATE = "run-state.json"
REQUEST: Final = "adjudication-part-0003-requests.jsonl"
SUBMISSION_INTENT: Final = ".adjudication-part-0003-submission-intent.json"
SUBMISSION_COMPLETED: Final = ".adjudication-part-0003-submission-completed.json"
OBSERVATION_OUTPUTS: Final = frozenset(
    {
        "adjudication-part-0003-output.jsonl",
        "adjudication-part-0003-api-errors.jsonl",
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
        "adjudication_completed_part_count",
        "observed_part_number",
        "active_batch_id",
        "active_input_file_id",
        "authorization_id",
        "authorization_sha256",
        "configuration_sha256",
        "estimated_adjudication_cost_microusd",
        "image_reference",
        "maximum_authorized_cost_microusd",
        "third_adjudication_submission_intent_sha256",
        "third_adjudication_submission_receipt_sha256",
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
        raise ThirdAdjudicationObservationError("observation cost invalid")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ThirdAdjudicationObservationError("observation cost invalid") from error
    if not decimal.is_finite() or decimal < 0:
        raise ThirdAdjudicationObservationError("observation cost invalid")
    result = int((decimal * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
    if result > contract.MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise ThirdAdjudicationObservationError("observation cost invalid")
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
        raise ThirdAdjudicationObservationError("observation evidence unavailable") from error


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
        raise ThirdAdjudicationObservationError("observation evidence unavailable") from error
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
        raise ThirdAdjudicationObservationError("observation evidence changed")
    return raw


def _decode(raw: bytes) -> dict[str, object]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=contract._pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ThirdAdjudicationObservationError("observation evidence invalid") from error
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise ThirdAdjudicationObservationError("observation evidence invalid")
    return value


def _read_request(stream: BinaryIO) -> dict[str, object]:
    raw = stream.readline(contract.REQUEST_MAX_BYTES + 1)
    if stream.read(1):
        raise ThirdAdjudicationObservationError("invalid observation request")
    try:
        return contract.parse_request(raw)
    except (TypeError, ValueError) as error:
        raise ThirdAdjudicationObservationError("invalid observation request") from error


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
        raise ThirdAdjudicationObservationError("observation authorization invalid") from error
    return _sha(raw)


def _run_directory(request: Mapping[str, object]) -> Path:
    _directory(RUNS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    object_root = RUNS_ROOT / f"sha256-{request['archive_sha256']}"
    runs = object_root / "review-runs"
    _directory(object_root, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    _directory(runs, mode=0o700, owner=(WORKER_UID, WORKER_GID))
    path = runs / str(request["run_id"])
    if path.resolve(strict=False).parent != runs.resolve(strict=True):
        raise ThirdAdjudicationObservationError("observation run invalid")
    _directory(path, mode=0o700, owner=(WORKER_UID, WORKER_GID))
    return path


def _inventory(
    run: Path,
) -> tuple[dict[str, bytes], dict[str, object], submit.SpeakerReviewRunState]:
    try:
        # Phase 73 exposes a complete, flat inventory together with the
        # validated state model.  Reuse that root-owned validation so this
        # boundary cannot accidentally widen the accepted run filesystem.
        inventory = submit._inventory(run)
        if not isinstance(inventory, tuple) or len(inventory) != 6:
            raise ValueError("inventory shape")
        contents, _, _, _, _, state_model = inventory
        canonical, loaded = load_validated_run_state(
            run,
            DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        )
        if loaded != state_model:
            raise ValueError("state model")
        state = state_model.to_dict()
        status = getattr(state_model.status, "value", state_model.status)
        observed = (
            status == "adjudication_part_completed"
            and state_model.adjudication_completed_part_count >= contract.OBSERVED_PART_NUMBER
        )
        if status == "failed":
            required, optional = submit._expected_inventory_names(state_model)
            optional.add(TERMINAL_OUTPUT)
        else:
            required, optional = worker._expected_inventory_names(
                state_model,
                observed=observed,
            )
    except Exception as error:
        raise ThirdAdjudicationObservationError("observation inventory invalid") from error
    if (
        canonical != run
        or not isinstance(state, dict)
        or _canonical(state) != _canonical(state_model.to_dict())
        or state_model.run_id != run.name
        or set(state) != observation._RUN_STATE_KEYS
        or not required <= set(contents)
        or not set(contents) <= required | optional
    ):
        raise ThirdAdjudicationObservationError("observation run state invalid")
    return contents, state, state_model


def _validate_submitted_state(state: Mapping[str, object]) -> None:
    try:
        batch_ids = state["adjudication_batch_ids"]
        input_ids = state["adjudication_input_file_ids"]
        if (
            state["status"] != "adjudication_submitted"
            or type(state["adjudication_part_count"]) is not int
            or state["adjudication_part_count"] <= contract.PREDECESSOR_COMPLETED_PART_COUNT
            or state["adjudication_completed_part_count"]
            != contract.PREDECESSOR_COMPLETED_PART_COUNT
            or not isinstance(batch_ids, list)
            or not isinstance(input_ids, list)
            or len(batch_ids) != contract.OBSERVED_PART_NUMBER
            or len(input_ids) != contract.OBSERVED_PART_NUMBER
            or state["adjudication_batch_id"] != batch_ids[-1]
            or state["adjudication_input_file_id"] != input_ids[-1]
            or len(set(batch_ids)) != contract.OBSERVED_PART_NUMBER
            or len(set(input_ids)) != contract.OBSERVED_PART_NUMBER
            or not all(observation._safe_text(value) for value in (*batch_ids, *input_ids))
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise ThirdAdjudicationObservationError("observation checkpoint invalid") from error


def _phase73_request(
    request: Mapping[str, object], intent: Mapping[str, object]
) -> tuple[dict[str, object], bytes, str]:
    """Load the exact Phase 73 submission authorization named by its intent."""

    authorization_id = intent.get("authorization_id")
    maximum = intent.get("maximum_authorized_cost_microusd")
    if not isinstance(authorization_id, str) or type(maximum) is not int:
        raise ValueError("authorization")
    raw = _stable(
        AUTHORIZATION_ROOT / f"{authorization_id}.json",
        maximum=submit_contract.REQUEST_MAX_BYTES,
        mode=0o600,
        owner=(ROOT_UID, ROOT_GID),
    )
    phase_request = submit_contract.parse_request(raw)
    expected = {
        "archive_sha256": request["archive_sha256"],
        "authorization_id": authorization_id,
        "maximum_authorized_cost_microusd": maximum,
        "operation": submit_contract.OPERATION,
        "purpose": submit_contract.PURPOSE,
        "run_id": request["run_id"],
        "schema_version": submit_contract.PROTOCOL_VERSION,
        "season_number": submit_contract.SEASON_NUMBER,
    }
    if phase_request != expected:
        raise ValueError("authorization")
    return phase_request, raw, _sha(raw)


def _validate_phase73_chain(
    request: Mapping[str, object],
    run: Path,
    contents: Mapping[str, bytes],
    state: submit.SpeakerReviewRunState,
) -> tuple[dict[str, object], str, str, str, int]:
    """Authenticate the complete Phase 73 submission predecessor."""

    _validate_submitted_state(state.to_dict())
    preparation_value, preparation_sha = preparation._validate_preparation(request)
    _directory(SUBMISSION_RECEIPTS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    intent, intent_sha = submit._record(
        SUBMISSION_RECEIPTS_ROOT / f"{request['run_id']}.intent.json"
    )
    receipt, receipt_sha = submit._record(SUBMISSION_RECEIPTS_ROOT / f"{request['run_id']}.json")
    if set(intent) != submit._ROOT_INTENT_KEYS or set(receipt) != submit._ROOT_RECEIPT_KEYS:
        raise ValueError("receipt keys")
    predecessor_request, predecessor_raw, predecessor_auth_sha = _phase73_request(request, intent)
    submit._validate_binding(intent, predecessor_request)
    predecessor_contents, predecessor_state = submit._pre_submission_snapshot(
        contents, state, intent
    )
    (
        predecessor_preparation,
        predecessor_preparation_sha,
        phase72_intent_sha,
        phase72_receipt_sha,
        predecessor_estimate,
        predecessor_actual,
    ) = submit._validate_phase72_predecessor(
        predecessor_request,
        run,
        predecessor_contents,
        predecessor_state,
    )
    result = submit._validate_receipt(receipt, intent=intent, contents=contents)
    submit._validate_replay_evidence(run, contents, state)
    if (
        predecessor_preparation != preparation_value
        or predecessor_preparation_sha != preparation_sha
        or intent.get("phase72_observation_intent_sha256") != phase72_intent_sha
        or intent.get("phase72_observation_receipt_sha256") != phase72_receipt_sha
        or intent.get("third_adjudication_part_number") != contract.OBSERVED_PART_NUMBER
        or result["run_id"] != state.run_id
        or result["run_status"] != "adjudication_submitted"
        or result["submitted_part_count"] != 1
        or result["adjudication_completed_part_count"] != contract.PREDECESSOR_COMPLETED_PART_COUNT
        or result["adjudication_part_count"] != state.adjudication_part_count
        or result["actual_primary_cost_microusd"] != _micros(state.actual_primary_cost_usd)
        or result["estimated_adjudication_cost_microusd"]
        != intent["estimated_adjudication_cost_microusd"]
        or result["estimated_adjudication_cost_microusd"] != predecessor_estimate
        or result["actual_primary_cost_microusd"] != predecessor_actual
        or _sha(predecessor_raw) != intent["authorization_sha256"]
        or _sha(predecessor_raw) != predecessor_auth_sha
    ):
        raise ValueError("predecessor binding")
    if any(name in contents for name in OBSERVATION_OUTPUTS | {TERMINAL_OUTPUT}):
        raise ValueError("observation output already present")
    estimated = result["estimated_adjudication_cost_microusd"]
    if type(estimated) is not int:
        raise ValueError("estimate")
    return preparation_value, preparation_sha, intent_sha, receipt_sha, estimated


def _validate_submission_predecessor(
    request: Mapping[str, object],
    run: Path,
    contents: Mapping[str, bytes],
    state: submit.SpeakerReviewRunState,
) -> tuple[dict[str, object], str, str, str, int]:
    """Validate Phase 73's root receipt and complete predecessor chain."""
    try:
        return _validate_phase73_chain(request, run, contents, state)
    except ThirdAdjudicationObservationError:
        raise
    except Exception as error:
        raise ThirdAdjudicationObservationError(
            "third-adjudication submission evidence invalid"
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
        "adjudication_completed_part_count": state["adjudication_completed_part_count"],
        "observed_part_number": state["adjudication_completed_part_count"] + 1,
        "active_batch_id": state["adjudication_batch_ids"][-1],
        "active_input_file_id": state["adjudication_input_file_ids"][-1],
        "authorization_id": request["authorization_id"],
        "authorization_sha256": authorization_sha256,
        "configuration_sha256": preparation["config_sha"],
        "estimated_adjudication_cost_microusd": estimated_adjudication_cost_microusd,
        "third_adjudication_submission_intent_sha256": submission_intent_sha256,
        "third_adjudication_submission_receipt_sha256": submission_receipt_sha256,
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
        raise ThirdAdjudicationObservationError("observation intent invalid")
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
        or value["adjudication_part_count"] <= contract.PREDECESSOR_COMPLETED_PART_COUNT
        or value.get("adjudication_completed_part_count")
        != contract.PREDECESSOR_COMPLETED_PART_COUNT
        or value.get("observed_part_number") != contract.OBSERVED_PART_NUMBER
        or not observation._safe_text(value.get("active_batch_id"), maximum=512)
        or not observation._safe_text(value.get("active_input_file_id"), maximum=512)
        or not observation._safe_text(value.get("pre_updated_at"), maximum=128)
    ):
        raise ThirdAdjudicationObservationError("observation intent invalid")
    for key in (
        "configuration_sha256",
        "third_adjudication_submission_intent_sha256",
        "third_adjudication_submission_receipt_sha256",
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
            raise ThirdAdjudicationObservationError("observation intent invalid")
    if not observation._safe_text(
        value.get("release_sha"), maximum=40
    ) or not observation._safe_text(value.get("image_reference"), maximum=512):
        raise ThirdAdjudicationObservationError("observation intent invalid")
    return dict(value)


def _validate_predecessors_from_intent(
    intent: Mapping[str, object],
    request: Mapping[str, object],
    run: Path,
    contents: Mapping[str, bytes],
    state: submit.SpeakerReviewRunState,
) -> None:
    # Replay validates the immutable submitted checkpoint reconstructed from
    # the intent, so the Phase 73 receipt is authenticated against exactly the
    # state that preceded this observation.  This also keeps replay provider
    # free: no provider-derived value is accepted from the generic intent.
    try:
        before, before_state = _pre_observation_snapshot(contents, state, intent)
        if _ISOLATED_ROOT:
            before_model = submit.SpeakerReviewRunState(before_state)
        else:
            from dataclasses import replace

            before_model = replace(
                state,
                status=type(state.status)("adjudication_submitted"),
                updated_at=before_state["updated_at"],
                adjudication_completed_part_count=contract.PREDECESSOR_COMPLETED_PART_COUNT,
            )
        (
            preparation_value,
            preparation_sha,
            submission_intent_sha,
            submission_receipt_sha,
            _estimate,
        ) = _validate_phase73_chain(request, run, before, before_model)
    except ThirdAdjudicationObservationError:
        raise
    except Exception as error:
        raise ThirdAdjudicationObservationError(
            "observation predecessor evidence invalid"
        ) from error
    if (
        intent.get("adjudication_part_count") != before_state.get("adjudication_part_count")
        or intent.get("adjudication_completed_part_count")
        != contract.PREDECESSOR_COMPLETED_PART_COUNT
        or intent.get("observed_part_number") != contract.OBSERVED_PART_NUMBER
        or intent.get("active_batch_id") != before_state.get("adjudication_batch_id")
        or intent.get("active_input_file_id") != before_state.get("adjudication_input_file_id")
        or intent.get("actual_primary_cost_microusd")
        != _micros(before_state.get("actual_primary_cost_usd"))
        or intent.get("pre_updated_at") != before_state.get("updated_at")
        or intent.get("archive_sha256") != request.get("archive_sha256")
        or intent.get("run_id") != request.get("run_id")
        or intent.get("maximum_authorized_cost_microusd")
        != request.get("maximum_authorized_cost_microusd")
        or intent.get("prep_receipt_sha256") != preparation_sha
        or intent.get("third_adjudication_submission_intent_sha256") != submission_intent_sha
        or intent.get("third_adjudication_submission_receipt_sha256") != submission_receipt_sha
        or preparation._active_binding()
        != (
            intent.get("release_sha"),
            intent.get("image_reference"),
            intent.get("configuration_sha256"),
        )
        or preparation_value.get("release_sha") != intent.get("release_sha")
        or preparation_value.get("image") != intent.get("image_reference")
    ):
        raise ThirdAdjudicationObservationError("observation predecessor evidence changed")


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
        raise ThirdAdjudicationObservationError("observation checkpoint changed")


def _pre_observation_snapshot(
    contents: Mapping[str, bytes],
    state: Mapping[str, object],
    intent: Mapping[str, object],
) -> tuple[dict[str, bytes], dict[str, object]]:
    before_state = dict(state)
    before_state.update(
        status="adjudication_submitted",
        updated_at=intent["pre_updated_at"],
        adjudication_completed_part_count=contract.PREDECESSOR_COMPLETED_PART_COUNT,
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
        raise ThirdAdjudicationObservationError("observation aggregate invalid") from error


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
        raise ThirdAdjudicationObservationError("observation receipt invalid")


def _safe_env() -> dict[str, str]:
    return {
        "PATH": "/usr/sbin:/usr/bin",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _environment_map(value: object, *, allow_none: bool = False) -> dict[str, str] | None:
    if value is None and allow_none:
        return {}
    if not isinstance(value, list):
        return None
    result: dict[str, str] = {}
    for item in value:
        if not isinstance(item, str) or "=" not in item:
            return None
        name, item_value = item.split("=", 1)
        if not name or name in result:
            return None
        result[name] = item_value
    return result


def _worker_environment(
    request: Mapping[str, object], binding: Mapping[str, object]
) -> dict[str, str]:
    return {
        contract.ENV_ARCHIVE_SHA256: str(request["archive_sha256"]),
        contract.ENV_RUN_ID: str(request["run_id"]),
        contract.ENV_AUTHORIZATION_ID: str(request["authorization_id"]),
        contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: str(
            request["maximum_authorized_cost_microusd"]
        ),
        contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: str(binding["pre_run_state_sha256"]),
        contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: str(binding["pre_artifact_set_sha256"]),
        contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: str(binding["pre_journal_set_sha256"]),
        contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256: str(binding["pre_output_set_sha256"]),
        contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256: str(binding["pre_derived_set_sha256"]),
        contract.ENV_EXPECTED_REQUEST_SHA256: str(binding["request_sha256"]),
    }


def _container_environment_is_exact(
    value: object,
    image_value: object,
    request: Mapping[str, object],
    binding: Mapping[str, object],
) -> bool:
    actual = _environment_map(value)
    image_environment = _environment_map(image_value, allow_none=True)
    if actual is None or image_environment is None:
        return False
    expected = {
        **image_environment,
        **WORKER_STATIC_ENVIRONMENT,
        **_worker_environment(request, binding),
    }
    return actual == expected and not FORBIDDEN_WORKER_ENVIRONMENT.intersection(actual)


def _container_identity_is_exact(
    request: Mapping[str, object],
    runs: Path,
    binding: Mapping[str, object],
) -> bool:
    try:
        expected_image = preparation._active_binding()[1]
        image_inspected = subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                "--format",
                "{{json .Config.Env}}",
                expected_image,
            ],
            cwd=RELEASE_ROOT,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            check=False,
            timeout=host.REVIEW_THIRD_ADJUDICATION_OBSERVATION_KILL_AFTER_SECONDS,
        )
        inspected = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .}}",
                host.REVIEW_THIRD_ADJUDICATION_OBSERVATION_CONTAINER_NAME,
            ],
            cwd=RELEASE_ROOT,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            check=False,
            timeout=host.REVIEW_THIRD_ADJUDICATION_OBSERVATION_KILL_AFTER_SECONDS,
        )
        if (
            image_inspected.returncode != 0
            or inspected.returncode != 0
            or len(image_inspected.stdout) > MAX_RECORD_BYTES
            or len(inspected.stdout) > MAX_RECORD_BYTES
        ):
            return False
        image_environment = json.loads(
            image_inspected.stdout.decode("utf-8"),
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
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
        value.get("Name") != f"/{host.REVIEW_THIRD_ADJUDICATION_OBSERVATION_CONTAINER_NAME}"
        or config.get("Image") != expected_image
        or config.get("User") != f"{WORKER_UID}:{WORKER_GID}"
        or config.get("WorkingDir") != host.REVIEW_THIRD_ADJUDICATION_OBSERVATION_CONTAINER_WORKDIR
        or config.get("Cmd") != list(host.REVIEW_THIRD_ADJUDICATION_OBSERVATION_CONTAINER_COMMAND)
        or not isinstance(labels, dict)
        or labels.get("com.docker.compose.service")
        != host.REVIEW_THIRD_ADJUDICATION_OBSERVATION_COMPOSE_SERVICE
        or labels.get("com.docker.compose.oneoff") != "True"
        or labels.get("com.docker.compose.project")
        != host.REVIEW_THIRD_ADJUDICATION_OBSERVATION_COMPOSE_PROJECT
        or labels.get("com.docker.compose.project.config_files") != os.fspath(COMPOSE_PATH)
        or labels.get("com.docker.compose.project.working_dir") != os.fspath(RELEASE_ROOT)
        or not _container_environment_is_exact(
            environment,
            image_environment,
            request,
            binding,
        )
        or host_config.get("ReadonlyRootfs") is not True
        or host_config.get("Privileged") is not False
        or host_config.get("CapDrop") != ["ALL"]
        or "no-new-privileges:true" not in (host_config.get("SecurityOpt") or [])
        or host_config.get("PidsLimit") != 128
        or not isinstance(networks, dict)
        or set(networks) != {host.REVIEW_THIRD_ADJUDICATION_OBSERVATION_NETWORK}
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
    run_id = str(request["run_id"])
    expected_destination = f"{WORKER_MOUNT}/{run_id}"
    expected_source = runs / run_id
    return (
        destinations.get(expected_destination) == (expected_source.as_posix(), True)
        and destinations.get(host.REVIEW_THIRD_ADJUDICATION_OBSERVATION_SECRET_TARGET, ("", True))[
            0
        ]
        != ""
        and destinations.get(host.REVIEW_THIRD_ADJUDICATION_OBSERVATION_SECRET_TARGET, ("", True))[
            1
        ]
        is False
        and destinations.get(host.REVIEW_THIRD_ADJUDICATION_OBSERVATION_TMP_TARGET) == ("", True)
        and set(destinations)
        == {
            expected_destination,
            host.REVIEW_THIRD_ADJUDICATION_OBSERVATION_SECRET_TARGET,
            host.REVIEW_THIRD_ADJUDICATION_OBSERVATION_TMP_TARGET,
        }
    )


def _cleanup_worker(
    request: Mapping[str, object],
    runs: Path,
    binding: Mapping[str, object],
) -> None:
    if not _container_identity_is_exact(request, runs, binding):
        return
    try:
        subprocess.run(
            ["docker", "rm", "--force", host.REVIEW_THIRD_ADJUDICATION_OBSERVATION_CONTAINER_NAME],
            cwd=RELEASE_ROOT,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            check=False,
            timeout=host.REVIEW_THIRD_ADJUDICATION_OBSERVATION_KILL_AFTER_SECONDS,
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
            process.wait(timeout=host.REVIEW_THIRD_ADJUDICATION_OBSERVATION_KILL_AFTER_SECONDS)
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
        host.REVIEW_THIRD_ADJUDICATION_OBSERVATION_COMPOSE_PROFILE,
        "-f",
        os.fspath(COMPOSE_PATH),
        "run",
        "--name",
        host.REVIEW_THIRD_ADJUDICATION_OBSERVATION_CONTAINER_NAME,
        "--rm",
        "--no-TTY",
        "--no-deps",
        "--pull",
        "never",
        "--user",
        f"{WORKER_UID}:{WORKER_GID}",
        "--volume",
        f"{(run_parent / str(request['run_id'])).as_posix()}:{WORKER_MOUNT}/{request['run_id']}:rw",
    ]
    for name, value in sorted(_worker_environment(request, binding).items()):
        args.extend(["--env", f"{name}={value}"])
    args.append(host.REVIEW_THIRD_ADJUDICATION_OBSERVATION_COMPOSE_SERVICE)
    return args


def _run_worker(
    request: Mapping[str, object], run_parent: Path, binding: Mapping[str, object]
) -> dict[str, object]:
    process: subprocess.Popen[bytes] | None = None
    try:
        _cleanup_worker(request, run_parent, binding)
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
                timeout=host.REVIEW_THIRD_ADJUDICATION_OBSERVATION_TIMEOUT_SECONDS - 60
            )
            out, err = stdout.result(timeout=5), stderr.result(timeout=5)
        if code != 0 or err or len(out) > contract.OUTPUT_MAX_BYTES:
            raise OSError
        return contract.parse_aggregate(out)
    except (OSError, subprocess.SubprocessError, TimeoutError, TypeError, ValueError) as error:
        raise ThirdAdjudicationObservationError("observation worker failed") from error
    finally:
        if process is not None and process.poll() is None:
            try:
                _terminate_worker(process)
            except (OSError, subprocess.SubprocessError):
                pass
        _cleanup_worker(request, run_parent, binding)


def _write_receipt(path: Path, value: Mapping[str, object]) -> None:
    _directory(OBSERVATION_RECEIPTS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    raw = _canonical(value)
    if len(raw) > MAX_RECORD_BYTES:
        raise ThirdAdjudicationObservationError("observation receipt too large")
    pending = path.with_name(f".{path.name}.pending")
    if os.path.lexists(path):
        submit._repair_linked_publication(path)
        existing, _ = _read_record(path)
        if existing != dict(value):
            raise ThirdAdjudicationObservationError("observation receipt conflict")
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
            raise ThirdAdjudicationObservationError("observation receipt conflict")
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
            raise ThirdAdjudicationObservationError("observation receipt unavailable") from error
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
            raise ThirdAdjudicationObservationError("observation receipt conflict") from None
        pending.unlink(missing_ok=True)
    except OSError as error:
        raise ThirdAdjudicationObservationError("observation receipt unavailable") from error


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
        raise ThirdAdjudicationObservationError("observation immutable state changed")
    if any(
        name != STATE and (name not in after or _sha(after[name]) != _sha(raw))
        for name, raw in before.items()
    ):
        raise ThirdAdjudicationObservationError("observation immutable evidence changed")
    added = set(after) - set(before)
    if status == "observed":
        if (
            added - OBSERVATION_OUTPUTS
            or after_state.get("status") != "adjudication_part_completed"
            or after_state.get("adjudication_completed_part_count")
            != before_state.get("adjudication_completed_part_count", 0) + 1
            or before_state.get("adjudication_completed_part_count")
            != contract.PREDECESSOR_COMPLETED_PART_COUNT
            or "adjudication-part-0003-output.jsonl" not in after
            or not observation._safe_text(after_state.get("updated_at"), maximum=128)
        ):
            raise ThirdAdjudicationObservationError("observation post-state invalid")
    elif status == "failed":
        if (
            added - {TERMINAL_OUTPUT}
            or after_state.get("status") != "failed"
            or after_state.get("adjudication_completed_part_count")
            != before_state.get("adjudication_completed_part_count")
            or TERMINAL_OUTPUT in before
            or not isinstance(after.get(TERMINAL_OUTPUT), bytes)
            or not after[TERMINAL_OUTPUT]
            or not observation._safe_text(after_state.get("updated_at"), maximum=128)
        ):
            raise ThirdAdjudicationObservationError("observation post-state invalid")
    elif status in {"waiting", "reconciliation_required"}:
        if added or before != after or before_state != after_state:
            raise ThirdAdjudicationObservationError("observation post-state invalid")
    else:
        raise ThirdAdjudicationObservationError("observation worker status invalid")


def _post_inventory(run: Path):
    """Re-read and compare the complete post-worker inventory."""

    first = _inventory(run)
    second = _inventory(run)
    if first != second:
        raise ThirdAdjudicationObservationError("observation post-inventory changed")
    return second


def process_request(request: Mapping[str, object]) -> dict[str, object]:
    try:
        request = contract.validate_request(request)
    except (TypeError, ValueError) as error:
        raise ThirdAdjudicationObservationError("invalid observation request") from error

    authorization_sha256 = _validate_authorization(request)
    run = _run_directory(request)
    _directory(OBSERVATION_RECEIPTS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    contents, state, state_model = _inventory(run)
    intent_path = OBSERVATION_RECEIPTS_ROOT / f"{request['run_id']}.intent.json"
    receipt_path = OBSERVATION_RECEIPTS_ROOT / f"{request['run_id']}.json"
    intent_exists = os.path.lexists(intent_path)
    receipt_exists = os.path.lexists(receipt_path)
    if receipt_exists and not intent_exists:
        raise ThirdAdjudicationObservationError("orphan observation receipt")

    submitted = (
        state.get("status") == "adjudication_submitted"
        and state.get("adjudication_completed_part_count")
        == contract.PREDECESSOR_COMPLETED_PART_COUNT
    )
    observed = (
        state.get("status") == "adjudication_part_completed"
        and state.get("adjudication_completed_part_count") == contract.OBSERVED_PART_NUMBER
    )
    failed = (
        state.get("status") == "failed"
        and state.get("adjudication_completed_part_count")
        == contract.PREDECESSOR_COMPLETED_PART_COUNT
    )
    if not (submitted or observed or failed):
        raise ThirdAdjudicationObservationError("observation checkpoint invalid")

    if observed or failed:
        if not intent_exists:
            raise ThirdAdjudicationObservationError("observation intent missing")
        intent = _validate_intent(
            _read_record(intent_path)[0],
            request=request,
            authorization_sha256=authorization_sha256,
        )
        before, before_state = _pre_observation_snapshot(contents, state, intent)
        _validate_predecessors_from_intent(
            intent,
            request,
            run,
            before,
            state_model,
        )
        terminal_status = "observed" if observed else "failed"
        _post_validate(before, contents, before_state, state, terminal_status)
        if terminal_status in {"observed", "failed"} and not receipt_exists:
            # A successful provider transition is not replayable from the
            # generic intent alone: it lacks authenticated provider output and
            # IDs.  Leave the run for explicit reconciliation rather than
            # fabricating a receipt after a crash.
            raise ThirdAdjudicationObservationError("observation reconciliation required")
        estimate = intent["estimated_adjudication_cost_microusd"]
        if type(estimate) is not int:
            raise ThirdAdjudicationObservationError("observation intent invalid")
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
        preparation_value,
        preparation_sha,
        submission_intent_sha,
        submission_receipt_sha,
        estimate,
    ) = _validate_submission_predecessor(request, run, contents, state_model)
    actual = _micros(state["actual_primary_cost_usd"])
    configured_maximum = submit._cost_micros(
        submit.DEFAULT_SPEAKER_REVIEW_CONFIGURATION.maximum_run_cost_usd
    )
    if (
        actual + estimate > request["maximum_authorized_cost_microusd"]
        or actual + estimate > configured_maximum
    ):
        raise ThirdAdjudicationObservationError("observation cost exceeds authorization")
    binding = _root_binding(
        request,
        authorization_sha256,
        preparation_value,
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
            raise ThirdAdjudicationObservationError("observation intent binding changed")
    else:
        if receipt_exists:
            raise ThirdAdjudicationObservationError("orphan observation receipt")
        intent = binding
        _write_receipt(intent_path, intent)
    if receipt_exists:
        raise ThirdAdjudicationObservationError("observation receipt state invalid")
    _validate_checkpoint_against_intent(contents, state, intent)
    _validate_predecessors_from_intent(
        intent,
        request,
        run,
        contents,
        state_model,
    )

    before, before_state = contents, state
    worker_result = _run_worker(request, run.parent, intent)
    # The dispatch lock serializes normal callers; read twice after Compose so
    # a concurrent worker/container cannot change evidence between validation
    # and receipt publication.  Any drift is fail-closed.
    after, after_state, _ = _post_inventory(run)
    status = str(worker_result.get("status"))
    if status not in {"waiting", "observed", "failed", "reconciliation_required"}:
        raise ThirdAdjudicationObservationError("observation worker result invalid")
    expected_completed = 3 if status == "observed" else 2
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
        raise ThirdAdjudicationObservationError("observation worker result invalid")
    _post_validate(before, after, before_state, after_state, status)
    if preparation._active_binding() != (
        preparation_value["release_sha"],
        preparation_value["image"],
        preparation_value["config_sha"],
    ):
        raise ThirdAdjudicationObservationError("active runtime changed")
    submit.phase69.phase68._source_workspace(request)
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
        raise ThirdAdjudicationObservationError("invalid observation caller")


def main() -> int:
    try:
        _require_root()
        sys.stdout.buffer.write(
            contract.canonical_json(process_request(_read_request(sys.stdin.buffer)))
        )
        return 0
    except Exception:
        sys.stderr.write("error=speaker_review_third_adjudication_observation_rejected\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
