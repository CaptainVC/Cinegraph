"""Root-only coordinator for exactly adjudication part-four submission.

The coordinator is the host half of the Phase 75 boundary. It authenticates
one canonical request, verifies the complete Phase 74 observation chain and
the digest-selected run, then starts one constrained Compose worker for exactly
adjudication part four. The application transition remains generic, but this
host command never observes output, submits a later part, parses a result,
advances review, enters final review, promotes, or ingests.
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
from dataclasses import replace
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from math import ceil
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

from cinegraph.common.speaker_review_cost_policy import (  # noqa: E402
    BATCH_COMPLETION_WINDOW,
    BATCH_DISCOUNT_MULTIPLIER,
    BATCH_ENDPOINT,
    ESTIMATED_CHARACTERS_PER_TOKEN,
    MAXIMUM_RUN_COST_USD,
    MODEL_TOKEN_PRICES,
)
from scripts import (  # noqa: E402
    private_speaker_review_fourth_adjudication_host_contract as host,
)
from scripts import (  # noqa: E402
    private_speaker_review_fourth_adjudication_submission_contract as contract,
)

# The root helper is deliberately executable with ``python -I -S -B``.  Do
# not import the application package here: its configuration imports optional
# Qdrant/OpenAI dependencies that are intentionally available only in the
# isolated Compose worker. Both paths reuse the established host predecessor
# checks; isolated execution uses a stdlib state view and journal validation.
from scripts import (  # noqa: E402
    private_speaker_review_third_adjudication_observation_contract as phase74_contract,
)
from scripts import (  # noqa: E402
    private_speaker_review_third_adjudication_observation_host_contract as phase74_host,
)

try:  # Application/state and provider worker imports are never required at root.
    # ``-S`` is the security boundary: skip application imports entirely in
    # isolated mode instead of probing optional host dependencies.
    if sys.flags.no_site:
        raise ModuleNotFoundError("isolated root")
    _config = importlib.import_module("cinegraph.config")
    _enum = importlib.import_module("cinegraph.domain.enums.enum")
    _workflow = importlib.import_module("cinegraph.ingestion.speaker_review.workflow")
    DEFAULT_SPEAKER_REVIEW_CONFIGURATION = _config.DEFAULT_SPEAKER_REVIEW_CONFIGURATION
    SpeakerReviewRunStatus = _enum.SpeakerReviewRunStatus
    SpeakerReviewRunState = _workflow.SpeakerReviewRunState
    load_validated_run_state = _workflow.load_validated_run_state
    from scripts import (  # noqa: E402
        run_private_speaker_review_first_adjudication as phase69,
    )
    from scripts import run_private_speaker_review_next_primary as preparation  # noqa: E402
    from scripts import (  # noqa: E402
        run_private_speaker_review_third_adjudication_observation as phase74,
    )
    from scripts import (  # noqa: E402
        submit_fourth_private_speaker_review_adjudication_workspace as worker,
    )
except ModuleNotFoundError:  # pragma: no cover - exercised by isolated launch test
    _ISOLATED_ROOT = True
    from dataclasses import dataclass
    from enum import Enum

    class SpeakerReviewRunStatus(str, Enum):
        ADJUDICATION_PART_COMPLETED = "adjudication_part_completed"
        ADJUDICATION_SUBMITTED = "adjudication_submitted"
        FAILED = "failed"

    @dataclass(frozen=True, slots=True)
    class SpeakerReviewRunState:
        """Small stdlib state view used only before the worker is started."""

        values: Mapping[str, object]

        def __getattr__(self, name: str) -> object:
            try:
                value = self.values[name]
            except KeyError as error:
                raise AttributeError(name) from error
            if name == "status" and isinstance(value, str):
                return SpeakerReviewRunStatus(value)
            if name in {"adjudication_batch_ids", "adjudication_input_file_ids"}:
                return tuple(value) if isinstance(value, list) else value
            return value

        def to_dict(self) -> dict[str, object]:
            return dict(self.values)

    def load_validated_run_state(
        run: Path, _configuration: object
    ) -> tuple[Path, SpeakerReviewRunState]:
        raw = (run / STATE_NAME).read_bytes()
        state = _decode(raw)
        state_keys = importlib.import_module(
            "scripts.run_private_speaker_review_observation"
        )._RUN_STATE_KEYS
        if state.get("run_id") != run.name or set(state) != state_keys:
            raise ValueError("run id")
        return run, SpeakerReviewRunState(state)

    preparation = importlib.import_module("scripts.run_private_speaker_review_next_primary")

    # These predecessor coordinators are intentionally stdlib-only and remain
    # usable under ``-I -S``. Keep their complete authentication and source
    # verification path instead of replacing it with reduced shims.
    phase69 = importlib.import_module("scripts.run_private_speaker_review_first_adjudication")
    phase74 = importlib.import_module(
        "scripts.run_private_speaker_review_third_adjudication_observation"
    )
    worker = types.SimpleNamespace(
        _expected_names=lambda state: _expected_inventory_names(state),
        _validate_checkpoint_shape=lambda state, *, submitted: _validate_state_shape(state),
        _validate_completed_parts=lambda run, contents, state, count: _validate_completed_evidence(
            run, contents, state
        ),
        _validate_journal=lambda run, contents, state, part, **kwargs: _stdlib_journal(
            contents, state, part, **kwargs
        ),
        _parse_requests=lambda contents, state: tuple(
            value
            for part in range(1, state.adjudication_part_count + 1)
            for value in (
                json.loads(line.decode("utf-8"))
                for line in contents[f"adjudication-part-{part:04d}-requests.jsonl"].splitlines()
                if line.strip()
            )
        ),
        _validate_replay_evidence=lambda *args, **kwargs: _validate_replay_evidence_stdlib(
            *args, **kwargs
        ),
    )
    DEFAULT_SPEAKER_REVIEW_CONFIGURATION = types.SimpleNamespace(
        maximum_run_cost_usd=MAXIMUM_RUN_COST_USD,
    )


class FourthAdjudicationSubmissionError(RuntimeError):
    """Generic path-free rejection at the privileged host boundary."""


# A compatibility spelling makes the operation easy to discover beside the
# Phase 69/70 coordinators without changing the public error surface.
FourthAdjudicationProcessingError = FourthAdjudicationSubmissionError

RELEASE_ROOT: Final = _ROOT
RUNS_ROOT: Final = host.SPEAKER_REVIEW_RUNS_ROOT
AUTHORIZATION_ROOT: Final = host.REVIEW_AUTHORIZATION_ROOT
PREPARATION_RECEIPTS_ROOT: Final = host.SPEAKER_REVIEW_ROOT / "receipts"
PHASE74_RECEIPTS_ROOT: Final = phase74_host.REVIEW_THIRD_ADJUDICATION_OBSERVATION_RECEIPTS_ROOT
RECEIPTS_ROOT: Final = host.REVIEW_FOURTH_ADJUDICATION_RECEIPTS_ROOT
ENV_FILE: Final = host.ENV_FILE
COMPOSE_PATH: Final = RELEASE_ROOT / "deploy/compose.yaml"
ROOT_UID: Final = 0
ROOT_GID: Final = 0
WORKER_UID: Final = host.REVIEW_FOURTH_ADJUDICATION_WORKER_UID
WORKER_GID: Final = host.REVIEW_FOURTH_ADJUDICATION_WORKER_GID
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
        "fourth_adjudication_part_number",
        "next_request_sha256",
        "operation",
        "phase74_observation_intent_sha256",
        "phase74_observation_receipt_sha256",
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
        raise FourthAdjudicationSubmissionError("adjudication evidence unavailable") from error


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
        raise FourthAdjudicationSubmissionError("adjudication evidence unavailable") from error
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
        raise FourthAdjudicationSubmissionError("adjudication evidence invalid") from error
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise FourthAdjudicationSubmissionError("adjudication evidence invalid")
    return value


def _record(path: Path) -> tuple[dict[str, object], str]:
    _repair_linked_publication(path)
    raw = _stable(path, maximum=MAX_RECORD_BYTES, mode=0o600, owner=(0, 0))
    return _decode(raw), _sha(raw)


def _fsync_directory(path: Path) -> None:
    """Durably publish root evidence without importing a predecessor."""

    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _repair_linked_publication(path: Path) -> None:
    """Finish only the authenticated hard-link publication after a crash."""

    pending = path.with_name(f".{path.name}.pending")
    if not os.path.lexists(pending):
        return
    try:
        published = path.lstat()
        staging = pending.lstat()
        if (
            not stat.S_ISREG(published.st_mode)
            or not stat.S_ISREG(staging.st_mode)
            or stat.S_ISLNK(published.st_mode)
            or stat.S_ISLNK(staging.st_mode)
            or (published.st_dev, published.st_ino) != (staging.st_dev, staging.st_ino)
            or published.st_nlink != 2
            or staging.st_nlink != 2
            or stat.S_IMODE(published.st_mode) != 0o600
            or (published.st_uid, published.st_gid) != (ROOT_UID, ROOT_GID)
        ):
            raise OSError
        pending.unlink()
        _fsync_directory(path.parent)
    except OSError as error:
        raise FourthAdjudicationSubmissionError("adjudication record unavailable") from error


def _read_request(stream: BinaryIO) -> dict[str, object]:
    raw = stream.readline(contract.REQUEST_MAX_BYTES + 1)
    if stream.read(1):
        raise FourthAdjudicationSubmissionError("invalid adjudication request")
    try:
        return contract.parse_request(raw)
    except (TypeError, ValueError) as error:
        raise FourthAdjudicationSubmissionError("invalid adjudication request") from error


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
        raise FourthAdjudicationSubmissionError("adjudication authorization invalid") from error
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
        raise FourthAdjudicationSubmissionError("adjudication run invalid") from error
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


def _state_dict(state: object) -> dict[str, object]:
    """Return the canonical state payload without importing workflow code."""

    if isinstance(state, Mapping):
        return dict(state)
    to_dict = getattr(state, "to_dict", None)
    if not callable(to_dict):
        raise ValueError("state")
    value = to_dict()
    if not isinstance(value, dict):
        raise ValueError("state")
    return value


def _state_value(state: object, name: str) -> object:
    if isinstance(state, Mapping):
        return state[name]
    return getattr(state, name)


def _expected_inventory_names(state: object) -> tuple[set[str], set[str]]:
    """Build the worker inventory contract from raw state fields.

    This mirrors the worker's name contract but intentionally lives in the
    root coordinator so a terminal ``failed`` checkpoint can be inspected
    without importing LangGraph/configuration dependencies.  It is also the
    source of truth when the worker module is unavailable under ``-S``.
    """

    completed = _state_value(state, "adjudication_completed_part_count")
    primary_parts = _state_value(state, "primary_part_count")
    adjudication_parts = _state_value(state, "adjudication_part_count")
    if not all(
        type(value) is int and value >= 0
        for value in (completed, primary_parts, adjudication_parts)
    ):
        raise ValueError("state counts")
    status = _state_value(state, "status")
    status_value = getattr(status, "value", status)
    journal_parts = (
        range(1, completed + 2)
        if status_value in {"adjudication_submitted", "failed"}
        else range(1, completed + 1)
    )
    required = {
        STATE_NAME,
        "candidates.jsonl",
        "source-manifest.json",
        *(f"primary-part-{part:04d}-requests.jsonl" for part in range(1, primary_parts + 1)),
        *(f"primary-part-{part:04d}-output.jsonl" for part in range(1, primary_parts + 1)),
        *(f"adjudication-part-{part:04d}-output.jsonl" for part in range(1, completed + 1)),
        *(
            f".primary-part-{part:04d}-submission-{kind}.json"
            for part in range(1, primary_parts + 1)
            for kind in ("intent", "completed")
        ),
        *(
            f"adjudication-part-{part:04d}-requests.jsonl"
            for part in range(1, adjudication_parts + 1)
        ),
        *(
            f".adjudication-part-{part:04d}-submission-{kind}.json"
            for part in journal_parts
            for kind in ("intent", "completed")
        ),
        "primary-verdicts.jsonl",
        "primary-parse-errors.json",
        "primary-decisions.jsonl",
    }
    optional = {
        *(f"primary-part-{part:04d}-api-errors.jsonl" for part in range(1, primary_parts + 1)),
        *(f"adjudication-part-{part:04d}-api-errors.jsonl" for part in range(1, completed + 1)),
    }
    if status_value == "adjudication_part_completed":
        next_part = completed + 1
        optional.update(
            {
                f".adjudication-part-{next_part:04d}-submission-intent.json",
                f".adjudication-part-{next_part:04d}-submission-completed.json",
            }
        )
    if status_value == "failed":
        required.add("terminal-api-errors.jsonl")
    return required, optional


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
        phase69_derived = extra["derived"]
        if (
            set(extra) != {"state", "derived"}
            or not isinstance(phase69_derived, dict)
            or any(not isinstance(name, str) or name.endswith("/") for name in phase69_derived)
        ):
            # The review-run contract is deliberately flat. Phase 69 records
            # every nested directory as a slash-suffixed derived entry, so
            # rejecting those entries also detects empty directories.
            raise ValueError
        canonical, state = load_validated_run_state(run, DEFAULT_SPEAKER_REVIEW_CONFIGURATION)
        if (
            canonical != run
            or not isinstance(state_payload, dict)
            or _canonical(state_payload) != _canonical(_state_dict(state))
            or _state_value(state, "run_id") != run.name
        ):
            raise ValueError
        # Use the worker's established contract for active checkpoints so its
        # richer tests/public API remain unchanged.  The worker deliberately
        # rejects terminal checkpoints, while the root must still inspect a
        # failed run to publish an evidence-bound failed receipt; use the
        # stdlib contract for that case (and whenever the worker is absent in
        # an isolated release).
        status = getattr(_state_value(state, "status"), "value", _state_value(state, "status"))
        if status == "failed":
            required, optional = _expected_inventory_names(state)
        else:
            try:
                required, optional = worker._expected_names(state)
            except (AttributeError, ModuleNotFoundError):
                required, optional = _expected_inventory_names(state)
        if not required <= set(files) or not set(files) <= required | optional:
            raise ValueError
        artifacts, journals, outputs, derived = _classes(files)
        total = sum(len(raw) for raw in files.values())
        if total > MAX_TOTAL_BYTES:
            raise ValueError
        return files, artifacts, journals, outputs, derived, state
    except FourthAdjudicationSubmissionError:
        raise
    except Exception as error:
        raise FourthAdjudicationSubmissionError("adjudication inventory invalid") from error


def _cost_micros(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FourthAdjudicationSubmissionError("adjudication cost invalid")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise FourthAdjudicationSubmissionError("adjudication cost invalid") from error
    if not decimal.is_finite() or decimal < 0:
        raise FourthAdjudicationSubmissionError("adjudication cost invalid")
    value_micros = int((decimal * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
    if value_micros > contract.MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise FourthAdjudicationSubmissionError("adjudication cost invalid")
    return value_micros


def _phase74_request(
    request: Mapping[str, object], intent: Mapping[str, object]
) -> tuple[dict[str, object], bytes, str]:
    """Load and authenticate the Phase 74 observation authorization."""
    authorization_id = intent.get("authorization_id")
    maximum = intent.get("maximum_authorized_cost_microusd")
    if not isinstance(authorization_id, str) or type(maximum) is not int:
        raise ValueError
    raw = _stable(
        AUTHORIZATION_ROOT / f"{authorization_id}.json",
        maximum=phase74_contract.REQUEST_MAX_BYTES,
        mode=0o600,
        owner=(ROOT_UID, ROOT_GID),
    )
    phase_request = phase74_contract.parse_request(raw)
    expected = {
        "archive_sha256": request["archive_sha256"],
        "authorization_id": authorization_id,
        "maximum_authorized_cost_microusd": maximum,
        "operation": phase74_contract.OPERATION,
        "purpose": phase74_contract.PURPOSE,
        "run_id": request["run_id"],
        "schema_version": phase74_contract.PROTOCOL_VERSION,
        "season_number": phase74_contract.SEASON_NUMBER,
    }
    if phase_request != expected:
        raise ValueError
    return phase_request, raw, _sha(raw)


def _validate_phase74_predecessor(
    request: Mapping[str, object],
    run: Path,
    contents: Mapping[str, bytes],
    state: SpeakerReviewRunState,
) -> tuple[dict[str, object], str, str, str, int, int]:
    """Authenticate Phase 74's observation receipt and its full prefix chain."""
    try:
        _directory(PHASE74_RECEIPTS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
        intent, intent_sha = _record(PHASE74_RECEIPTS_ROOT / f"{request['run_id']}.intent.json")
        receipt, receipt_sha = _record(PHASE74_RECEIPTS_ROOT / f"{request['run_id']}.json")
        if set(intent) != phase74._BINDING_KEYS or set(receipt) != phase74._RECEIPT_KEYS:
            raise ValueError
        phase_request, phase_auth_raw, phase_auth_sha = _phase74_request(request, intent)
        validated_intent = phase74._validate_intent(
            intent, request=phase_request, authorization_sha256=phase_auth_sha
        )
        # Reconstruct the exact submitted checkpoint that preceded observation.
        pre_payload = _state_dict(state)
        pre_payload.update(
            status=SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED.value,
            updated_at=intent["pre_updated_at"],
            adjudication_completed_part_count=phase74_contract.PREDECESSOR_COMPLETED_PART_COUNT,
        )
        if _ISOLATED_ROOT:
            pre_state = SpeakerReviewRunState(pre_payload)
        else:
            pre_state = replace(
                state,
                status=SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED,
                updated_at=intent["pre_updated_at"],
                adjudication_completed_part_count=phase74_contract.PREDECESSOR_COMPLETED_PART_COUNT,
            )
        pre_contents = dict(contents)
        pre_contents.pop("adjudication-part-0003-output.jsonl", None)
        pre_contents.pop("adjudication-part-0003-api-errors.jsonl", None)
        pre_contents.pop("terminal-api-errors.jsonl", None)
        pre_contents[STATE_NAME] = _canonical(pre_payload)
        phase74._validate_submission_predecessor(phase_request, run, pre_contents, pre_state)
        result = phase74_contract.validate_aggregate(receipt.get("result"), status="observed")
        phase74._validate_final_receipt(
            receipt, intent=validated_intent, result=result, contents=contents
        )
        if (
            result["run_id"] != state.run_id
            or result["adjudication_part_count"] != state.adjudication_part_count
            or result["adjudication_completed_part_count"]
            != phase74_contract.OBSERVED_PART_NUMBER
            or result["run_status"] != SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED.value
            or _cost_micros(state.actual_primary_cost_usd) != result["actual_primary_cost_microusd"]
            or result["estimated_adjudication_cost_microusd"]
            != intent["estimated_adjudication_cost_microusd"]
            or _sha(phase_auth_raw) != intent["authorization_sha256"]
        ):
            raise ValueError
        preparation_value, preparation_sha = preparation._validate_preparation(request)
        estimate = result["estimated_adjudication_cost_microusd"]
        actual = result["actual_primary_cost_microusd"]
        if type(estimate) is not int or type(actual) is not int:
            raise ValueError
        return preparation_value, preparation_sha, intent_sha, receipt_sha, estimate, actual
    except FourthAdjudicationSubmissionError:
        raise
    except Exception as error:
        raise FourthAdjudicationSubmissionError("phase 74 predecessor invalid") from error


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
        if _ISOLATED_ROOT:
            previous_payload = state.to_dict()
            previous_payload.update(
                status=SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED.value,
                updated_at=intent["pre_updated_at"],
                adjudication_batch_id=state.adjudication_batch_ids[-2],
                adjudication_input_file_id=state.adjudication_input_file_ids[-2],
                adjudication_batch_ids=list(state.adjudication_batch_ids[:-1]),
                adjudication_input_file_ids=list(state.adjudication_input_file_ids[:-1]),
            )
            previous = SpeakerReviewRunState(previous_payload)
        else:
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
        raise FourthAdjudicationSubmissionError("adjudication replay invalid") from error


def _receipt_paths(run_id: str, part: int) -> tuple[Path, Path]:
    if not isinstance(run_id, str) or not run_id or type(part) is not int or part <= 0:
        raise FourthAdjudicationSubmissionError("adjudication receipt path invalid")
    # Phase 74 authenticates the third completed part. This command is fixed
    # to target part four, so one run-scoped pair is sufficient.
    if part != contract.SUBMITTED_PART_NUMBER:
        raise FourthAdjudicationSubmissionError("adjudication receipt path invalid")
    return (
        RECEIPTS_ROOT / f"{run_id}.intent.json",
        RECEIPTS_ROOT / f"{run_id}.json",
    )


def _validate_state_shape(state: SpeakerReviewRunState) -> None:
    try:
        submitted = state.status is SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED
        if _ISOLATED_ROOT:
            count = state.adjudication_completed_part_count
            total = state.adjudication_part_count
            ids = state.adjudication_batch_ids
            inputs = state.adjudication_input_file_ids
            expected = count + 1 if submitted else count
            if (
                type(count) is not int
                or type(total) is not int
                or count <= 0
                or (count >= total and not (count == total == contract.PREDECESSOR_COMPLETED_PART_COUNT
                                             and not submitted))
                or not isinstance(ids, tuple)
                or not isinstance(inputs, tuple)
                or len(ids) != expected
                or len(inputs) != expected
                or len(set(ids)) != len(ids)
                or len(set(inputs)) != len(inputs)
                or not all(
                    isinstance(value, str) and value == value.strip() and value for value in ids
                )
                or not all(
                    isinstance(value, str) and value == value.strip() and value for value in inputs
                )
                or state.adjudication_batch_id != ids[-1]
                or state.adjudication_input_file_id != inputs[-1]
            ):
                raise ValueError
        else:
            worker._validate_checkpoint_shape(state, submitted=submitted)
    except Exception as error:
        raise FourthAdjudicationSubmissionError("adjudication checkpoint invalid") from error
    if state.adjudication_part_count < contract.PREDECESSOR_COMPLETED_PART_COUNT:
        raise FourthAdjudicationSubmissionError("no adjudication part remains")
    # Phase 74 authenticated observation of part three; this boundary
    # authorizes exactly part four and never generalizes to later parts.
    if state.adjudication_completed_part_count != contract.PREDECESSOR_COMPLETED_PART_COUNT:
        raise FourthAdjudicationSubmissionError("adjudication predecessor invalid")
    if state.status not in {
        SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED,
        SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED,
    }:
        raise FourthAdjudicationSubmissionError("adjudication checkpoint invalid")


def _validate_completed_evidence(
    run: Path,
    contents: Mapping[str, bytes],
    state: SpeakerReviewRunState,
) -> None:
    try:
        if _ISOLATED_ROOT:
            ids = state.adjudication_batch_ids
            inputs = state.adjudication_input_file_ids
            for part in range(1, state.adjudication_completed_part_count + 1):
                request = contents[f"adjudication-part-{part:04d}-requests.jsonl"]
                binding = {
                    "schema_version": 1,
                    "request_sha256": _sha(request),
                    "run_id": state.run_id,
                    "stage": "adjudication",
                    "part": part,
                    "prompt_version": state.prompt_version,
                    "batch_endpoint": BATCH_ENDPOINT,
                    "completion_window": BATCH_COMPLETION_WINDOW,
                }
                intent = _decode(contents[f".adjudication-part-{part:04d}-submission-intent.json"])
                completed = _decode(
                    contents[f".adjudication-part-{part:04d}-submission-completed.json"]
                )
                if (
                    intent.get("binding") != binding
                    or completed.get("binding") != binding
                    or completed.get("batch_id") != ids[part - 1]
                    or completed.get("input_file_id") != inputs[part - 1]
                    or f"adjudication-part-{part:04d}-output.jsonl" not in contents
                ):
                    raise ValueError
        else:
            worker._validate_completed_parts(
                run, contents, state, state.adjudication_completed_part_count
            )
    except Exception as error:
        raise FourthAdjudicationSubmissionError("adjudication evidence invalid") from error


def _stdlib_journal(
    contents: Mapping[str, bytes],
    state: SpeakerReviewRunState,
    part: int,
    *,
    expected_batch: str,
    expected_input: str,
) -> None:
    request = contents[f"adjudication-part-{part:04d}-requests.jsonl"]
    binding = {
        "schema_version": 1,
        "request_sha256": _sha(request),
        "run_id": state.run_id,
        "stage": "adjudication",
        "part": part,
        "prompt_version": state.prompt_version,
        "batch_endpoint": BATCH_ENDPOINT,
        "completion_window": BATCH_COMPLETION_WINDOW,
    }
    intent = _decode(contents[f".adjudication-part-{part:04d}-submission-intent.json"])
    completed = _decode(contents[f".adjudication-part-{part:04d}-submission-completed.json"])
    if (
        intent.get("binding") != binding
        or completed.get("binding") != binding
        or completed.get("batch_id") != expected_batch
        or completed.get("input_file_id") != expected_input
    ):
        raise ValueError("journal")


def _validate_replay_evidence_stdlib(
    run: Path, contents: Mapping[str, bytes], state: SpeakerReviewRunState
) -> None:
    """Validate completed-prefix and active-part evidence without the worker."""

    if state.status is not SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED:
        raise ValueError("checkpoint")
    _validate_state_shape(state)
    ids = state.adjudication_batch_ids
    inputs = state.adjudication_input_file_ids
    for part in range(1, state.adjudication_completed_part_count + 1):
        _stdlib_journal(
            contents,
            state,
            part,
            expected_batch=ids[part - 1],
            expected_input=inputs[part - 1],
        )
        if f"adjudication-part-{part:04d}-output.jsonl" not in contents:
            raise ValueError("output")
    active = state.adjudication_completed_part_count + 1
    _stdlib_journal(
        contents,
        state,
        active,
        expected_batch=ids[-1],
        expected_input=inputs[-1],
    )
    if any(
        f"adjudication-part-{active:04d}-{suffix}" in contents
        for suffix in ("output.jsonl", "api-errors.jsonl")
    ):
        raise ValueError("active output")


def _estimate_cost(contents: Mapping[str, bytes], state: SpeakerReviewRunState) -> int:
    try:
        if _ISOLATED_ROOT:
            requests = worker._parse_requests(contents, state)
            if not requests:
                raise ValueError
            input_characters = 0
            output_tokens = 0
            for request in requests:
                body = request.get("body")
                if not isinstance(body, dict) or type(body.get("max_output_tokens")) is not int:
                    raise ValueError
                input_characters += len(json.dumps(body, ensure_ascii=False, separators=(",", ":")))
                output_tokens += body["max_output_tokens"]
            input_tokens = ceil(input_characters / ESTIMATED_CHARACTERS_PER_TOKEN)
            input_price, output_price = MODEL_TOKEN_PRICES[state.adjudication_model]
            estimate = (
                (input_tokens * input_price + output_tokens * output_price)
                / 1_000_000
                * BATCH_DISCOUNT_MULTIPLIER
            )
            return _cost_micros(estimate)
        requests = worker._parse_requests(contents, state)
        return _cost_micros(
            worker.estimate_batch_cost_usd(
                requests=requests,
                model=worker.DEFAULT_MODEL_CONFIGURATION.speaker_adjudication_model,
                configuration=worker.DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
            )
        )
    except FourthAdjudicationSubmissionError:
        raise
    except Exception as error:
        raise FourthAdjudicationSubmissionError("adjudication cost invalid") from error


def _binding(
    request: Mapping[str, object],
    *,
    authorization_sha256: str,
    preparation_value: Mapping[str, object],
    preparation_sha256: str,
    phase74_intent_sha256: str,
    phase74_receipt_sha256: str,
    estimated: int,
    contents: Mapping[str, bytes],
    state: SpeakerReviewRunState,
) -> dict[str, object]:
    artifacts, journals, outputs, derived = _classes(contents)
    next_part = state.adjudication_completed_part_count + 1
    next_name = _OUTPUT_NAME.format(part=next_part).replace("-output", "-requests")
    if next_name not in contents:
        raise FourthAdjudicationSubmissionError("adjudication request unavailable")
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
        "fourth_adjudication_part_number": next_part,
        "next_request_sha256": _sha(contents[next_name]),
        "operation": contract.OPERATION,
        "phase74_observation_intent_sha256": phase74_intent_sha256,
        "phase74_observation_receipt_sha256": phase74_receipt_sha256,
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
            {key: value for key, value in state.to_dict().items() if key not in ROOT_STATE_MUTABLE}
        )
    )


def _validate_binding(value: object, request: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _ROOT_INTENT_KEYS:
        raise FourthAdjudicationSubmissionError("adjudication intent invalid")
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
        or type(value.get("fourth_adjudication_part_number")) is not int
        or value["adjudication_completed_part_count"]
        != contract.PREDECESSOR_COMPLETED_PART_COUNT
        or value["fourth_adjudication_part_number"] != contract.SUBMITTED_PART_NUMBER
        or value["fourth_adjudication_part_number"] != value["adjudication_completed_part_count"] + 1
        or not isinstance(value.get("pre_updated_at"), str)
        or not value["pre_updated_at"].strip()
    ):
        raise FourthAdjudicationSubmissionError("adjudication intent invalid")
    if not (
        0 < value["adjudication_completed_part_count"] < value["adjudication_part_count"]
        and value["actual_primary_cost_microusd"] >= 0
        and value["estimated_adjudication_cost_microusd"] >= 0
    ):
        raise FourthAdjudicationSubmissionError("adjudication intent invalid")
    for name in (
        "authorization_sha256",
        "configuration_sha256",
        "next_request_sha256",
        "phase74_observation_intent_sha256",
        "phase74_observation_receipt_sha256",
        "pre_artifact_set_sha256",
        "pre_derived_set_sha256",
        "pre_journal_set_sha256",
        "pre_output_set_sha256",
        "pre_run_state_sha256",
        "pre_state_binding_sha256",
        "prep_receipt_sha256",
    ):
        if not _is_sha(value.get(name)):
            raise FourthAdjudicationSubmissionError("adjudication intent invalid")
    return dict(value)


def _write_once(path: Path, value: Mapping[str, object]) -> None:
    _directory(RECEIPTS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    raw = _canonical(value)
    if len(raw) > MAX_RECORD_BYTES:
        raise FourthAdjudicationSubmissionError("adjudication receipt unavailable")
    if os.path.lexists(path):
        existing, _ = _record(path)
        if existing != dict(value):
            raise FourthAdjudicationSubmissionError("adjudication receipt conflict")
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
            _fsync_directory(RECEIPTS_ROOT)
        os.link(pending, path, follow_symlinks=False)
        _fsync_directory(RECEIPTS_ROOT)
        pending.unlink()
        _fsync_directory(RECEIPTS_ROOT)
    except FileExistsError:
        existing, _ = _record(path)
        if existing != dict(value):
            raise FourthAdjudicationSubmissionError("adjudication receipt conflict") from None
        pending.unlink(missing_ok=True)
    except OSError as error:
        raise FourthAdjudicationSubmissionError("adjudication receipt unavailable") from error
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


def _worker_environment(
    request: Mapping[str, object], bindings: Mapping[str, str]
) -> dict[str, str]:
    return {
        contract.ENV_ARCHIVE_SHA256: str(request["archive_sha256"]),
        contract.ENV_RUN_ID: str(request["run_id"]),
        contract.ENV_AUTHORIZATION_ID: str(request["authorization_id"]),
        contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: str(
            request["maximum_authorized_cost_microusd"]
        ),
        **dict(bindings),
    }


def _worker_args(
    request: Mapping[str, object], run_parent: Path, bindings: Mapping[str, str]
) -> list[str]:
    environment = _worker_environment(request, bindings)
    command = [
        "docker",
        "compose",
        "--progress",
        "quiet",
        "--env-file",
        os.fspath(ENV_FILE),
        "--profile",
        host.REVIEW_FOURTH_ADJUDICATION_COMPOSE_PROFILE,
        "-f",
        os.fspath(COMPOSE_PATH),
        "run",
        "--rm",
        "--no-deps",
        "--no-TTY",
        "--pull",
        "never",
        "--name",
        host.REVIEW_FOURTH_ADJUDICATION_CONTAINER_NAME,
        "--user",
        f"{WORKER_UID}:{WORKER_GID}",
    ]
    for name, value in sorted(environment.items()):
        command.extend(("--env", f"{name}={value}"))
    command.extend(
        (
            "--volume",
            f"{(run_parent / str(request['run_id'])).as_posix()}:{(host.REVIEW_FOURTH_ADJUDICATION_RUNS_TARGET / str(request['run_id'])).as_posix()}:rw",
            host.REVIEW_FOURTH_ADJUDICATION_COMPOSE_SERVICE,
        )
    )
    return command


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


def _container_environment_is_exact(
    value: object,
    image_value: object,
    request: Mapping[str, object],
    bindings: Mapping[str, str],
) -> bool:
    actual = _environment_map(value)
    image_environment = _environment_map(image_value, allow_none=True)
    if actual is None or image_environment is None:
        return False
    expected = {
        **image_environment,
        **WORKER_STATIC_ENVIRONMENT,
        **_worker_environment(request, bindings),
    }
    return actual == expected and not FORBIDDEN_WORKER_ENVIRONMENT.intersection(actual)


def _container_identity_is_exact(
    request: Mapping[str, object], runs: Path, bindings: Mapping[str, str]
) -> bool:
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
                host.REVIEW_FOURTH_ADJUDICATION_COMPOSE_PROFILE,
                "-f",
                os.fspath(COMPOSE_PATH),
                "config",
                "--images",
                host.REVIEW_FOURTH_ADJUDICATION_COMPOSE_SERVICE,
            ],
            cwd=RELEASE_ROOT,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=host.REVIEW_FOURTH_ADJUDICATION_KILL_AFTER_SECONDS,
        )
        image = expected.stdout.decode("utf-8").strip()
        image_inspected = subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                "--format",
                "{{json .Config.Env}}",
                image,
            ],
            cwd=RELEASE_ROOT,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=host.REVIEW_FOURTH_ADJUDICATION_KILL_AFTER_SECONDS,
        )
        inspected = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .}}",
                host.REVIEW_FOURTH_ADJUDICATION_CONTAINER_NAME,
            ],
            cwd=RELEASE_ROOT,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=host.REVIEW_FOURTH_ADJUDICATION_KILL_AFTER_SECONDS,
        )
        value = json.loads(inspected.stdout.decode("utf-8"))
        config = value.get("Config", {})
        host_config = value.get("HostConfig", {})
        settings = value.get("NetworkSettings", {})
        labels = config.get("Labels", {})
        networks = settings.get("Networks", {})
        mounts = value.get("Mounts", [])
        image_environment = json.loads(image_inspected.stdout.decode("utf-8"))
        if not isinstance(mounts, list):
            return False
        if (
            inspected.returncode != 0
            or expected.returncode != 0
            or image_inspected.returncode != 0
            or not image
            or value.get("Name") != f"/{host.REVIEW_FOURTH_ADJUDICATION_CONTAINER_NAME}"
            or config.get("Image") != image
            or config.get("User") != f"{WORKER_UID}:{WORKER_GID}"
            or config.get("WorkingDir") != host.REVIEW_FOURTH_ADJUDICATION_CONTAINER_WORKDIR
            or config.get("Cmd") != list(host.REVIEW_FOURTH_ADJUDICATION_CONTAINER_COMMAND)
            or labels.get("com.docker.compose.service")
            != host.REVIEW_FOURTH_ADJUDICATION_COMPOSE_SERVICE
            or labels.get("com.docker.compose.oneoff") != "True"
            or labels.get("com.docker.compose.project")
            != host.REVIEW_FOURTH_ADJUDICATION_COMPOSE_PROJECT
            or labels.get("com.docker.compose.project.config_files") != os.fspath(COMPOSE_PATH)
            or labels.get("com.docker.compose.project.working_dir") != os.fspath(RELEASE_ROOT)
            or host_config.get("ReadonlyRootfs") is not True
            or host_config.get("Privileged") is not False
            or host_config.get("CapDrop") != ["ALL"]
            or "no-new-privileges:true" not in (host_config.get("SecurityOpt") or [])
            or host_config.get("PidsLimit") != 128
            or not _container_environment_is_exact(
                config.get("Env"), image_environment, request, bindings
            )
            or not isinstance(networks, dict)
            or set(networks) != {host.REVIEW_FOURTH_ADJUDICATION_NETWORK}
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
        expected_destination = host.REVIEW_FOURTH_ADJUDICATION_RUNS_TARGET
        expected_source = runs
        run_id = str(request["run_id"])
        expected_destination = expected_destination / run_id
        expected_source = expected_source / run_id
        return (
            destinations.get(expected_destination.as_posix()) == (expected_source.as_posix(), True)
            and destinations.get(host.REVIEW_FOURTH_ADJUDICATION_SECRET_TARGET, ("", True))[1]
            is False
            and destinations.get(host.REVIEW_FOURTH_ADJUDICATION_TMP_TARGET) == ("", True)
            and set(destinations)
            == {
                expected_destination.as_posix(),
                host.REVIEW_FOURTH_ADJUDICATION_SECRET_TARGET,
                host.REVIEW_FOURTH_ADJUDICATION_TMP_TARGET,
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


def _cleanup_worker(request: Mapping[str, object], runs: Path, bindings: Mapping[str, str]) -> None:
    if not _container_identity_is_exact(request, runs, bindings):
        return
    try:
        subprocess.run(
            ["docker", "rm", "--force", host.REVIEW_FOURTH_ADJUDICATION_CONTAINER_NAME],
            cwd=RELEASE_ROOT,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=host.REVIEW_FOURTH_ADJUDICATION_KILL_AFTER_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return


def _terminate_worker(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=host.REVIEW_FOURTH_ADJUDICATION_KILL_AFTER_SECONDS)
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
        _cleanup_worker(request, run_parent, bindings)
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
                code = process.wait(timeout=host.REVIEW_FOURTH_ADJUDICATION_TIMEOUT_SECONDS - 60)
            except subprocess.TimeoutExpired as error:
                _terminate_worker(process)
                output.result(timeout=5)
                errors.result(timeout=5)
                raise FourthAdjudicationSubmissionError("adjudication worker timeout") from error
            stdout, stderr = output.result(timeout=5), errors.result(timeout=5)
        if code != 0 or stderr or len(stdout) > contract.OUTPUT_MAX_BYTES:
            raise OSError
        try:
            return contract.parse_aggregate(stdout)
        except (TypeError, ValueError) as error:
            raise FourthAdjudicationSubmissionError("adjudication aggregate invalid") from error
    except FourthAdjudicationSubmissionError:
        raise
    except (OSError, subprocess.SubprocessError, TimeoutError) as error:
        raise FourthAdjudicationSubmissionError("adjudication worker failed") from error
    finally:
        if process is not None and process.poll() is None:
            try:
                _terminate_worker(process)
            except (OSError, subprocess.SubprocessError):
                pass
        _cleanup_worker(request, run_parent, bindings)


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


def _post_inventory(run: Path):
    """Re-read and compare the complete post-worker inventory."""

    first = _inventory(run)
    second = _inventory(run)
    if first != second:
        raise FourthAdjudicationSubmissionError("adjudication post-inventory changed")
    return second


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
        raise FourthAdjudicationSubmissionError("adjudication worker result invalid") from error
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
        or result["adjudication_completed_part_count"] != before.adjudication_completed_part_count
        or result["estimated_adjudication_cost_microusd"] != estimated
        or result["actual_primary_cost_microusd"] != actual
        or result["submitted_part_count"] != (0 if status == "reconciliation_required" else 1)
    ):
        raise FourthAdjudicationSubmissionError("adjudication worker result invalid")
    return result


def _state_transition(
    before: SpeakerReviewRunState,
    after: SpeakerReviewRunState,
) -> None:
    before_payload = before.to_dict()
    after_payload = after.to_dict()
    if set(before_payload) != set(after_payload):
        raise FourthAdjudicationSubmissionError("adjudication post-state invalid")
    if any(
        before_payload[key] != after_payload[key]
        for key in before_payload
        if key not in ROOT_STATE_MUTABLE
    ):
        raise FourthAdjudicationSubmissionError("adjudication immutable state changed")
    if (
        before.status is not SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED
        or after.status is not SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED
        or after.adjudication_completed_part_count != before.adjudication_completed_part_count
        or len(after.adjudication_batch_ids) != len(before.adjudication_batch_ids) + 1
        or len(after.adjudication_input_file_ids) != len(before.adjudication_input_file_ids) + 1
        or after.adjudication_batch_ids[:-1] != before.adjudication_batch_ids
        or after.adjudication_input_file_ids[:-1] != before.adjudication_input_file_ids
        or after.adjudication_batch_id != after.adjudication_batch_ids[-1]
        or after.adjudication_input_file_id != after.adjudication_input_file_ids[-1]
    ):
        raise FourthAdjudicationSubmissionError("adjudication post-state invalid")


def _post_validate(
    before_files: Mapping[str, bytes],
    after_files: Mapping[str, bytes],
    before: SpeakerReviewRunState,
    after: SpeakerReviewRunState,
    *,
    result_status: str,
) -> None:
    for name, raw in before_files.items():
        if name != STATE_NAME and (name not in after_files or _sha(after_files[name]) != _sha(raw)):
            raise FourthAdjudicationSubmissionError("adjudication immutable evidence changed")
    added = set(after_files) - set(before_files)
    part = before.adjudication_completed_part_count + 1
    allowed_journals = {
        _INTENT_NAME.format(part=part),
        _COMPLETED_NAME.format(part=part),
    }
    if result_status in {"submitted", "already_submitted"}:
        if added - allowed_journals:
            raise FourthAdjudicationSubmissionError("adjudication post-inventory invalid")
        if any(
            name in after_files
            for name in (_OUTPUT_NAME.format(part=part), _API_ERRORS_NAME.format(part=part))
        ):
            raise FourthAdjudicationSubmissionError("adjudication output invalid")
        if result_status == "submitted":
            _state_transition(before, after)
        elif before != after:
            raise FourthAdjudicationSubmissionError("adjudication replay changed state")
    elif result_status == "reconciliation_required":
        if added or before_files != after_files or before != after:
            raise FourthAdjudicationSubmissionError("adjudication reconciliation changed state")
    else:
        raise FourthAdjudicationSubmissionError("adjudication worker result invalid")


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
        raise FourthAdjudicationSubmissionError("adjudication receipt invalid")
    if (
        any(value.get(key) != expected for key, expected in intent.items() if key != "status")
        or value.get("status") != "receipt"
    ):
        raise FourthAdjudicationSubmissionError("adjudication receipt invalid")
    expected = _receipt_payload(intent, value.get("result", {}), contents)
    if value != expected:
        raise FourthAdjudicationSubmissionError("adjudication receipt invalid")
    try:
        return contract.validate_aggregate(value["result"])
    except (TypeError, ValueError) as error:
        raise FourthAdjudicationSubmissionError("adjudication receipt invalid") from error


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
        raise FourthAdjudicationSubmissionError("adjudication aggregate invalid") from error


def process_request(request: Mapping[str, object]) -> dict[str, object]:
    try:
        request = contract.validate_request(request)
        authorization_sha = _validate_authorization(request)
        run = _run_directory(request)
        contents, artifacts, journals, outputs, derived, state = _inventory(run)
        _validate_state_shape(state)
        _validate_completed_evidence(run, contents, state)
        intent_path, receipt_path = _receipt_paths(
            str(request["run_id"]), state.adjudication_completed_part_count + 1
        )
        intent_exists = os.path.lexists(intent_path)
        receipt_exists = os.path.lexists(receipt_path)
        intent_pending_exists = os.path.lexists(
            intent_path.with_name(f".{intent_path.name}.pending")
        )
        receipt_pending_exists = os.path.lexists(
            receipt_path.with_name(f".{receipt_path.name}.pending")
        )
        if receipt_exists and not intent_exists:
            raise FourthAdjudicationSubmissionError("orphan adjudication receipt")

        # Phase 74's receipt binds the checkpoint before part-four
        # submission. Reconstruct that checkpoint first on replay; validating
        # Phase 74 directly against the four-ID submitted state is invalid.
        predecessor_contents = contents
        predecessor_state = state
        replay_intent: dict[str, object] | None = None
        if state.status is SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED:
            if not intent_exists:
                raise FourthAdjudicationSubmissionError("adjudication intent missing")
            replay_intent, _ = _record(intent_path)
            _validate_binding(replay_intent, request)
            predecessor_contents, predecessor_state = _pre_submission_snapshot(
                contents, state, replay_intent
            )
        (
            preparation_value,
            preparation_sha,
            phase74_intent_sha,
            phase74_receipt_sha,
            prior_estimate,
            prior_actual,
        ) = _validate_phase74_predecessor(request, run, predecessor_contents, predecessor_state)
        if _cost_micros(state.actual_primary_cost_usd) != prior_actual:
            raise FourthAdjudicationSubmissionError("adjudication predecessor cost changed")
        if (
            state.status is SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED
            and state.adjudication_part_count == contract.PREDECESSOR_COMPLETED_PART_COUNT
        ):
            # Phase 74 has already authenticated and completed the final
            # adjudication part. This exact operation is an aggregate-only,
            # provider-free acknowledgement; it must not create an intent,
            # read the secret, launch Compose, or mutate the run.
            next_part = contract.SUBMITTED_PART_NUMBER
            if (
                intent_exists
                or receipt_exists
                or intent_pending_exists
                or receipt_pending_exists
                or any(
                name in contents
                for name in (
                    _INTENT_NAME.format(part=next_part),
                    _COMPLETED_NAME.format(part=next_part),
                )
                )
            ):
                raise FourthAdjudicationSubmissionError(
                    "adjudication completion evidence ambiguous"
                )
            if preparation._active_binding() != (
                preparation_value["release_sha"],
                preparation_value["image"],
                preparation_value["config_sha"],
            ):
                raise FourthAdjudicationSubmissionError("active runtime changed")
            return _aggregate(
                request,
                state,
                status="all_parts_completed",
                estimated=0,
                submitted=0,
            )
        estimated = _estimate_cost(contents, state)
        if estimated != prior_estimate:
            raise FourthAdjudicationSubmissionError("adjudication estimate changed")
        maximum = int(request["maximum_authorized_cost_microusd"])
        configured = _cost_micros(DEFAULT_SPEAKER_REVIEW_CONFIGURATION.maximum_run_cost_usd)
        if prior_actual + prior_estimate > maximum or prior_actual + prior_estimate > configured:
            raise FourthAdjudicationSubmissionError("adjudication cost exceeds authorization")
        # A submitted checkpoint is replay-only.  Its application journals and
        # request binding are checked by the worker's provider-disabled path;
        # this coordinator merely repairs a missing root receipt.
        if state.status is SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED:
            if replay_intent is None:
                raise FourthAdjudicationSubmissionError("adjudication intent missing")
            intent = replay_intent
            pre_contents, pre_state = predecessor_contents, predecessor_state
            recomputed = _binding(
                request,
                authorization_sha256=authorization_sha,
                preparation_value=preparation_value,
                preparation_sha256=preparation_sha,
                phase74_intent_sha256=phase74_intent_sha,
                phase74_receipt_sha256=phase74_receipt_sha,
                estimated=estimated,
                contents=pre_contents,
                state=pre_state,
            )
            if intent != recomputed:
                raise FourthAdjudicationSubmissionError("adjudication intent binding changed")
            try:
                worker._validate_replay_evidence(run, contents, state)
            except Exception as error:
                raise FourthAdjudicationSubmissionError("adjudication replay invalid") from error
            if preparation._active_binding() != (
                preparation_value["release_sha"],
                preparation_value["image"],
                preparation_value["config_sha"],
            ):
                raise FourthAdjudicationSubmissionError("active runtime changed")
            if receipt_exists:
                receipt, _ = _record(receipt_path)
                result = _validate_receipt(receipt, intent=intent, contents=contents)
                if (
                    result["run_id"] != request["run_id"]
                    or result["estimated_adjudication_cost_microusd"] != estimated
                    or result["actual_primary_cost_microusd"] != prior_actual
                ):
                    raise FourthAdjudicationSubmissionError("adjudication receipt invalid")
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
            after, after_artifacts, after_journals, after_outputs, after_derived, after_state = (
                _post_inventory(run)
            )
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
                raise FourthAdjudicationSubmissionError("adjudication replay invalid") from error
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
            raise FourthAdjudicationSubmissionError("adjudication output invalid")
        fresh_contents = _fresh_binding_contents(contents, state)
        binding = _binding(
            request,
            authorization_sha256=authorization_sha,
            preparation_value=preparation_value,
            preparation_sha256=preparation_sha,
            phase74_intent_sha256=phase74_intent_sha,
            phase74_receipt_sha256=phase74_receipt_sha,
            estimated=estimated,
            contents=fresh_contents,
            state=state,
        )
        if intent_exists:
            intent, _ = _record(intent_path)
            if intent != binding:
                raise FourthAdjudicationSubmissionError("adjudication intent binding changed")
        else:
            if receipt_exists:
                raise FourthAdjudicationSubmissionError("orphan adjudication receipt")
            _write_once(intent_path, binding)
            intent = binding
        if receipt_exists:
            raise FourthAdjudicationSubmissionError("adjudication receipt state invalid")
        worker_result = _run_worker(
            request,
            run.parent,
            _checkpoint_bindings(contents, artifacts, journals, outputs, derived, state),
        )
        checked_status = worker_result.get("status")
        if checked_status not in {"submitted", "reconciliation_required"}:
            raise FourthAdjudicationSubmissionError("adjudication worker result invalid")
        _validate_worker_result(
            worker_result,
            request=request,
            before=state,
            status=str(checked_status),
            estimated=estimated,
            actual=prior_actual,
        )
        after, after_artifacts, after_journals, after_outputs, after_derived, after_state = (
            _post_inventory(run)
        )
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
            raise FourthAdjudicationSubmissionError("adjudication post-state invalid") from error
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
            raise FourthAdjudicationSubmissionError("active runtime changed")
        _write_once(receipt_path, _receipt_payload(intent, result, after))
        return result
    except FourthAdjudicationSubmissionError:
        raise
    except Exception as error:
        raise FourthAdjudicationSubmissionError("adjudication evidence invalid") from error


def _require_root() -> None:
    if (
        os.name != "posix"
        or not hasattr(os, "geteuid")
        or os.geteuid() != ROOT_UID
        or os.environ.get("SUDO_USER") != host.REVIEW_USER
        or platform.system() != "Linux"
        or platform.machine() != "x86_64"
    ):
        raise FourthAdjudicationSubmissionError("invalid adjudication caller")


def main() -> int:
    try:
        _require_root()
        result = process_request(_read_request(sys.stdin.buffer))
        sys.stdout.buffer.write(contract.canonical_json(result))
        return 0
    except Exception:
        sys.stderr.write("error=speaker_review_fourth_adjudication_rejected\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
