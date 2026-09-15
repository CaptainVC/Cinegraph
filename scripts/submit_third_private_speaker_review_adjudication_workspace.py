"""Submit exactly Terra adjudication part three.

This egress worker receives only a digest-bound ``review-runs`` parent and an
OpenAI Compose secret. It invokes the reusable ``k + 1`` transition only from
the independently pinned completed-count-two checkpoint and never observes,
parses, advances, finalizes, or ingests a review.
"""

from __future__ import annotations

# Imports intentionally follow the isolated release bootstrap below.
# ruff: noqa: E402, I001

import hashlib
import json
import math
import os
import stat
import sys
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from typing import Mapping

_ROOT = Path(__file__).resolve().parents[1]
for _root in (_ROOT, _ROOT / "src"):
    if os.fspath(_root) not in sys.path:
        sys.path.insert(0, os.fspath(_root))

from cinegraph.adapters.llm.openai_speaker_review_batch_gateway import (  # noqa: E402
    OpenAISpeakerReviewBatchGateway,
)
from cinegraph.adapters.workflow.langgraph.speaker_review_graph import (  # noqa: E402
    SpeakerReviewGraphWorkflow,
)
from cinegraph.config import (  # noqa: E402
    DEFAULT_MODEL_CONFIGURATION,
    DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
)
from cinegraph.domain.enums.enum import SpeakerReviewRunStatus  # noqa: E402
from cinegraph.ingestion.speaker_review.costs import estimate_batch_cost_usd  # noqa: E402
from cinegraph.ingestion.speaker_review.workflow import (  # noqa: E402
    SpeakerReviewRunState,
    SpeakerReviewWorkflow,
    _read_submission_record,
    _submission_path,
    load_validated_run_state,
)
from cinegraph.ports.llm.speaker_review_batch_gateway import (  # noqa: E402
    BatchSnapshot,
    BatchSubmission,
)
from scripts import private_speaker_review_third_adjudication_submission_contract as contract  # noqa: E402

PRIVATE_REVIEW_RUNS_ROOT = Path("/review-workspace/review-runs")
OPENAI_SECRET_PATH = Path("/run/secrets/openai_api_key")
SECRET_MAX_BYTES = 4_096
STATE_MAX_BYTES = 64 * 1024
ARTIFACT_MAX_BYTES = 64 * 1024 * 1024
TOTAL_MAX_BYTES = 256 * 1024 * 1024
STATE = "run-state.json"


class ThirdAdjudicationSubmissionWorkerError(RuntimeError):
    """Generic failure whose private details never cross the worker boundary."""


def _identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_nlink)


def _stable(path: Path, maximum: int) -> bytes:
    descriptor = -1
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > maximum
        ):
            raise OSError
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        raw = os.read(descriptor, maximum + 1)
        after = path.lstat()
        if _identity(before) != _identity(opened) or _identity(opened) != _identity(after):
            raise OSError
        if len(raw) != opened.st_size:
            raise OSError
        return raw
    except OSError as error:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication evidence unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def read_stable_openai_secret(path: Path = OPENAI_SECRET_PATH) -> str:
    """Read the Compose secret once, rejecting links, mutation, and unsafe mode."""

    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o400
            or (os.name == "posix" and (metadata.st_uid, metadata.st_gid) != (os.geteuid(), os.getegid()))
        ):
            raise OSError
        raw = _stable(path, SECRET_MAX_BYTES)
    except OSError as error:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication secret unavailable") from error
    try:
        secret = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication secret unavailable") from error
    if secret.endswith("\n"):
        secret = secret[:-1]
    if not secret or any(character.isspace() for character in secret):
        raise ThirdAdjudicationSubmissionWorkerError("adjudication secret unavailable")
    return secret


def _parse_cost_cap(value: str) -> int:
    try:
        parsed = int(value, 10)
    except (TypeError, ValueError) as error:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication request invalid") from error
    if str(parsed) != value or parsed <= 0 or parsed > contract.MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication request invalid")
    return parsed


def request_from_environment(environment: Mapping[str, str] | None = None) -> dict[str, object]:
    values = os.environ if environment is None else environment
    try:
        return contract.validate_request(
            {
                "archive_sha256": values.get(contract.ENV_ARCHIVE_SHA256, ""),
                "authorization_id": values.get(contract.ENV_AUTHORIZATION_ID, ""),
                "maximum_authorized_cost_microusd": _parse_cost_cap(
                    values.get(contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD, "")
                ),
                "operation": contract.OPERATION,
                "purpose": contract.PURPOSE,
                "run_id": values.get(contract.ENV_RUN_ID, ""),
                "schema_version": contract.PROTOCOL_VERSION,
                "season_number": contract.SEASON_NUMBER,
            }
        )
    except (TypeError, ValueError) as error:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication request invalid") from error


def _run_directory(run_id: str, review_root: Path = PRIVATE_REVIEW_RUNS_ROOT) -> Path:
    try:
        root = review_root.resolve(strict=True)
        candidate = review_root / run_id
        metadata = candidate.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or candidate.resolve(strict=True) != candidate
            or candidate.resolve(strict=False).parent != root
        ):
            raise OSError
        return candidate.resolve(strict=True)
    except OSError as error:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication run unavailable") from error


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _set_digest(contents: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(contents):
        encoded = name.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(contents[name]).to_bytes(8, "big"))
        digest.update(contents[name])
    return digest.hexdigest()


def _expected_names(state: SpeakerReviewRunState) -> tuple[set[str], set[str]]:
    completed = state.adjudication_completed_part_count
    # A completed checkpoint has no active-part journal yet.  During crash
    # recovery the worker may find one or both active-part journal records;
    # those records are admitted to the inventory and reconciled below.  A
    # submitted checkpoint, by contrast, must include the active part's
    # intent/completed pair so replay can validate the provider binding.
    journal_parts = (
        range(1, completed + 2)
        if state.status is SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED
        else range(1, completed + 1)
    )
    required = {
        STATE,
        "candidates.jsonl",
        "source-manifest.json",
        *(f"primary-part-{part:04d}-requests.jsonl" for part in range(1, state.primary_part_count + 1)),
        *(f"primary-part-{part:04d}-output.jsonl" for part in range(1, state.primary_part_count + 1)),
        *(f"adjudication-part-{part:04d}-output.jsonl" for part in range(1, completed + 1)),
        *(f".primary-part-{part:04d}-submission-{kind}.json" for part in range(1, state.primary_part_count + 1) for kind in ("intent", "completed")),
        *(f"adjudication-part-{part:04d}-requests.jsonl" for part in range(1, state.adjudication_part_count + 1)),
        *(f".adjudication-part-{part:04d}-submission-{kind}.json" for part in journal_parts for kind in ("intent", "completed")),
        "primary-verdicts.jsonl",
        "primary-parse-errors.json",
        "primary-decisions.jsonl",
    }
    optional = {
        *(f"primary-part-{part:04d}-api-errors.jsonl" for part in range(1, state.primary_part_count + 1)),
        *(f"adjudication-part-{part:04d}-api-errors.jsonl" for part in range(1, state.adjudication_completed_part_count + 1)),
    }
    if state.status is SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED:
        next_part = completed + 1
        optional.update(
            {
                f".adjudication-part-{next_part:04d}-submission-intent.json",
                f".adjudication-part-{next_part:04d}-submission-completed.json",
                f"adjudication-part-{next_part:04d}-output.jsonl",
                f"adjudication-part-{next_part:04d}-api-errors.jsonl",
            }
        )
    return required, optional


def _inventory(run: Path) -> tuple[dict[str, bytes], SpeakerReviewRunState]:
    try:
        names = {entry.name for entry in run.iterdir()}
    except OSError as error:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication inventory unavailable") from error
    if STATE not in names:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication inventory invalid")
    try:
        canonical, state = load_validated_run_state(run, DEFAULT_SPEAKER_REVIEW_CONFIGURATION)
        if canonical != run or state.run_id != run.name:
            raise ValueError
    except Exception as error:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication run state invalid") from error
    required, optional = _expected_names(state)
    if not required <= names or not names <= required | optional:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication inventory invalid")
    contents: dict[str, bytes] = {}
    total = 0
    for name in sorted(names):
        raw = _stable(run / name, STATE_MAX_BYTES if name == STATE else ARTIFACT_MAX_BYTES)
        total += len(raw)
        if total > TOTAL_MAX_BYTES:
            raise ThirdAdjudicationSubmissionWorkerError("adjudication inventory too large")
        contents[name] = raw
    try:
        if json.loads(contents[STATE].decode("utf-8")) != state.to_dict():
            raise ValueError
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication run state invalid") from error
    if state.status not in {
        SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED,
        SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED,
    }:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication checkpoint invalid")
    return contents, state


def _digest_bindings(contents: Mapping[str, bytes], environment: Mapping[str, str]) -> None:
    required = (
        contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256,
        contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256,
        contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256,
        contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256,
        contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256,
    )
    expected = {name: environment.get(name, "") for name in required}
    if any(
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in expected.values()
    ):
        raise ThirdAdjudicationSubmissionWorkerError("adjudication checkpoint binding invalid")
    artifacts: dict[str, bytes] = {}
    journals: dict[str, bytes] = {}
    outputs: dict[str, bytes] = {}
    derived: dict[str, bytes] = {}
    for name, raw in contents.items():
        if name == STATE:
            continue
        if name.startswith("."):
            journals[name] = raw
        elif name in {"candidates.jsonl", "source-manifest.json"} or (
            name.startswith("primary-part-") and name.endswith("-requests.jsonl")
        ):
            artifacts[name] = raw
        elif name.endswith("-output.jsonl") or name.endswith("-api-errors.jsonl"):
            outputs[name] = raw
        else:
            derived[name] = raw
    actual = {
        contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: _sha(contents[STATE]),
        contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: _set_digest(artifacts),
        contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: _set_digest(journals),
        contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256: _set_digest(outputs),
        contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256: _set_digest(derived),
    }
    if actual != expected:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication checkpoint changed")


def _expected_request_hash(
    contents: Mapping[str, bytes], state: SpeakerReviewRunState, environment: Mapping[str, str]
) -> str:
    expected = environment.get(contract.ENV_EXPECTED_REQUEST_SHA256, "")
    if not isinstance(expected, str) or len(expected) != 64 or any(
        character not in "0123456789abcdef" for character in expected
    ):
        raise ThirdAdjudicationSubmissionWorkerError("adjudication request binding invalid")
    part = state.adjudication_completed_part_count + 1
    name = f"adjudication-part-{part:04d}-requests.jsonl"
    if name not in contents or _sha(contents[name]) != expected:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication request changed")
    return expected


def _parse_requests(
    contents: Mapping[str, bytes], state: SpeakerReviewRunState
) -> tuple[dict[str, object], ...]:
    requests: list[dict[str, object]] = []
    for part in range(1, state.adjudication_part_count + 1):
        raw = contents.get(f"adjudication-part-{part:04d}-requests.jsonl")
        if raw is None:
            raise ThirdAdjudicationSubmissionWorkerError("adjudication request unavailable")
        try:
            for line in raw.splitlines():
                value = json.loads(line.decode("utf-8"), object_pairs_hook=contract._pairs)
                if not isinstance(value, dict):
                    raise ValueError
                requests.append(value)
        except (UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise ThirdAdjudicationSubmissionWorkerError("adjudication request invalid") from error
    if not requests:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication request unavailable")
    return tuple(requests)


def _cost_micros(value: float) -> int:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ThirdAdjudicationSubmissionWorkerError("adjudication cost invalid")
    try:
        micros = Decimal(str(value)) * Decimal(1_000_000)
    except (InvalidOperation, ValueError) as error:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication cost invalid") from error
    if micros < 0:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication cost invalid")
    result = int(micros.to_integral_value(rounding=ROUND_CEILING))
    if result > contract.MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication cost invalid")
    return result


def _aggregate(
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
                "run_id": state.run_id,
                "run_status": state.status.value,
                "season_number": contract.SEASON_NUMBER,
                "status": status,
                "submitted_part_count": submitted,
            },
            status=status,
        )
    except (TypeError, ValueError) as error:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication aggregate invalid") from error


def _journal_binding(
    request: bytes,
    state: SpeakerReviewRunState,
    part: int,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "request_sha256": _sha(request),
        "run_id": state.run_id,
        "stage": "adjudication",
        "part": part,
        "prompt_version": state.prompt_version,
        "batch_endpoint": DEFAULT_SPEAKER_REVIEW_CONFIGURATION.batch_endpoint,
        "completion_window": DEFAULT_SPEAKER_REVIEW_CONFIGURATION.batch_completion_window,
    }


def _validate_journal(
    run: Path,
    contents: Mapping[str, bytes],
    state: SpeakerReviewRunState,
    part: int,
    *,
    expected_batch: str,
    expected_input: str,
) -> None:
    try:
        request = contents[f"adjudication-part-{part:04d}-requests.jsonl"]
        intent = _read_submission_record(
            _submission_path(run, "adjudication", part - 1, "intent"), completed=False
        )
        completed = _read_submission_record(
            _submission_path(run, "adjudication", part - 1, "completed"), completed=True
        )
    except (KeyError, RuntimeError) as error:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication evidence invalid") from error
    binding = _journal_binding(request, state, part)
    if (
        intent is None
        or completed is None
        or intent.get("binding") != binding
        or completed.get("binding") != binding
        or completed.get("batch_id") != expected_batch
        or completed.get("input_file_id") != expected_input
    ):
        raise ThirdAdjudicationSubmissionWorkerError("adjudication evidence invalid")


def _validate_completed_parts(
    run: Path, contents: Mapping[str, bytes], state: SpeakerReviewRunState, count: int
) -> None:
    ids = state.adjudication_batch_ids
    inputs = state.adjudication_input_file_ids
    if not isinstance(ids, tuple) or not isinstance(inputs, tuple) or len(ids) < count or len(inputs) < count:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication checkpoint invalid")
    for part in range(1, count + 1):
        _validate_journal(
            run,
            contents,
            state,
            part,
            expected_batch=ids[part - 1],
            expected_input=inputs[part - 1],
        )
        if f"adjudication-part-{part:04d}-output.jsonl" not in contents:
            raise ThirdAdjudicationSubmissionWorkerError("adjudication output unavailable")


def _validate_checkpoint_shape(state: SpeakerReviewRunState, *, submitted: bool) -> None:
    count = state.adjudication_completed_part_count
    total = state.adjudication_part_count
    ids = state.adjudication_batch_ids
    inputs = state.adjudication_input_file_ids
    expected = count + 1 if submitted else count
    if (
        type(count) is not int
        or type(total) is not int
        or count != 2
        or count >= total
        or not isinstance(ids, tuple)
        or not isinstance(inputs, tuple)
        or len(ids) != expected
        or len(inputs) != expected
        or len(set(ids)) != len(ids)
        or len(set(inputs)) != len(inputs)
        or not all(isinstance(value, str) and value and value == value.strip() for value in ids)
        or not all(isinstance(value, str) and value and value == value.strip() for value in inputs)
        or state.adjudication_batch_id != ids[-1]
        or state.adjudication_input_file_id != inputs[-1]
    ):
        raise ThirdAdjudicationSubmissionWorkerError("adjudication checkpoint invalid")


def _validate_replay_evidence(
    run: Path, contents: Mapping[str, bytes], state: SpeakerReviewRunState
) -> None:
    _validate_checkpoint_shape(state, submitted=True)
    _validate_completed_parts(run, contents, state, state.adjudication_completed_part_count)
    part = state.adjudication_completed_part_count + 1
    _validate_journal(
        run,
        contents,
        state,
        part,
        expected_batch=state.adjudication_batch_ids[-1],
        expected_input=state.adjudication_input_file_ids[-1],
    )
    if any(
        f"adjudication-part-{part:04d}-{suffix}" in contents
        for suffix in ("output.jsonl", "api-errors.jsonl")
    ):
        raise ThirdAdjudicationSubmissionWorkerError("adjudication replay output invalid")


def _workflow(secret: str, *, expected_request_sha256: str) -> SpeakerReviewGraphWorkflow:
    from openai import OpenAI

    models = DEFAULT_MODEL_CONFIGURATION
    review = SpeakerReviewWorkflow(
        gateway=OpenAISpeakerReviewBatchGateway(
            OpenAI(api_key=secret), DEFAULT_SPEAKER_REVIEW_CONFIGURATION
        ),
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        primary_model=models.speaker_review_model,
        adjudication_model=models.speaker_adjudication_model,
        final_review_model=models.speaker_final_review_model,
        primary_reasoning_effort=models.speaker_review_reasoning_effort,
        adjudication_reasoning_effort=models.speaker_adjudication_reasoning_effort,
        final_review_reasoning_effort=models.speaker_final_review_reasoning_effort,
        expected_next_adjudication_request_sha256=expected_request_sha256,
    )
    return SpeakerReviewGraphWorkflow(review)


class _ProviderDisabledGateway:
    """Gateway used by replay validation; no provider call is possible."""

    def submit(self, *args: object, **kwargs: object) -> BatchSubmission:
        raise AssertionError("provider access disabled during replay")

    def retrieve(self, *args: object, **kwargs: object) -> BatchSnapshot:
        raise AssertionError("provider access disabled during replay")

    def download_file(self, *args: object, **kwargs: object) -> str:
        raise AssertionError("provider access disabled during replay")


def _replay_workflow(
    *, expected_request_sha256: str | None = None
) -> SpeakerReviewGraphWorkflow:
    models = DEFAULT_MODEL_CONFIGURATION
    review = SpeakerReviewWorkflow(
        gateway=_ProviderDisabledGateway(),
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        primary_model=models.speaker_review_model,
        adjudication_model=models.speaker_adjudication_model,
        final_review_model=models.speaker_final_review_model,
        primary_reasoning_effort=models.speaker_review_reasoning_effort,
        adjudication_reasoning_effort=models.speaker_adjudication_reasoning_effort,
        final_review_reasoning_effort=models.speaker_final_review_reasoning_effort,
        expected_next_adjudication_request_sha256=expected_request_sha256,
    )
    return SpeakerReviewGraphWorkflow(review)


def submit_third_adjudication(
    *,
    environment: Mapping[str, str] | None = None,
    secret_path: Path = OPENAI_SECRET_PATH,
    review_root: Path = PRIVATE_REVIEW_RUNS_ROOT,
) -> dict[str, object]:
    values = os.environ if environment is None else environment
    request = request_from_environment(environment)
    run = _run_directory(str(request["run_id"]), review_root)
    contents, state = _inventory(run)
    _digest_bindings(contents, values)
    expected_request_hash = _expected_request_hash(contents, state, values)
    if state.adjudication_completed_part_count != 2:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication predecessor invalid")
    if state.adjudication_part_count <= 2:
        raise ThirdAdjudicationSubmissionWorkerError("no adjudication part remains")
    requests = _parse_requests(contents, state)
    try:
        estimate = estimate_batch_cost_usd(
            requests=requests,
            model=DEFAULT_MODEL_CONFIGURATION.speaker_adjudication_model,
            configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        )
    except Exception as error:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication cost invalid") from error
    estimate_micros = _cost_micros(estimate)
    actual_primary = _cost_micros(state.actual_primary_cost_usd)
    cap = int(request["maximum_authorized_cost_microusd"])
    configured = _cost_micros(DEFAULT_SPEAKER_REVIEW_CONFIGURATION.maximum_run_cost_usd)
    if actual_primary + estimate_micros > cap or actual_primary + estimate_micros > configured:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication cost exceeds authorization")

    if state.status is SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED:
        _validate_replay_evidence(run, contents, state)
        try:
            replay_directory, replay_state = _replay_workflow().submit_next_adjudication(
                run, verified_run_state=state
            )
        except RuntimeError as error:
            raise ThirdAdjudicationSubmissionWorkerError("adjudication replay invalid") from error
        if replay_directory != run or replay_state != state:
            raise ThirdAdjudicationSubmissionWorkerError("adjudication replay invalid")
        return _aggregate(
            state,
            status="already_submitted",
            estimated=estimate_micros,
            submitted=1,
        )

    if state.status is not SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication checkpoint invalid")
    _validate_checkpoint_shape(state, submitted=False)
    _validate_completed_parts(run, contents, state, state.adjudication_completed_part_count)
    next_part = state.adjudication_completed_part_count + 1
    intent_name = f".adjudication-part-{next_part:04d}-submission-intent.json"
    completed_name = f".adjudication-part-{next_part:04d}-submission-completed.json"
    if any(
        name in contents
        for name in (
            f"adjudication-part-{next_part:04d}-output.jsonl",
            f"adjudication-part-{next_part:04d}-api-errors.jsonl",
        )
    ):
        raise ThirdAdjudicationSubmissionWorkerError("adjudication checkpoint invalid")
    if intent_name in contents or completed_name in contents:
        if intent_name not in contents or completed_name not in contents:
            return _aggregate(
                state,
                status="reconciliation_required",
                estimated=estimate_micros,
                submitted=0,
            )
        try:
            recovered_directory, recovered_state = _replay_workflow(
                expected_request_sha256=expected_request_hash
            ).submit_next_adjudication(run, verified_run_state=state)
        except RuntimeError as error:
            if contract.is_reconciliation_error(error):
                return _aggregate(
                    state,
                    status="reconciliation_required",
                    estimated=estimate_micros,
                    submitted=0,
                )
            raise ThirdAdjudicationSubmissionWorkerError("adjudication replay invalid") from error
        except AssertionError as error:
            raise ThirdAdjudicationSubmissionWorkerError("adjudication replay invalid") from error
        if (
            recovered_directory != run
            or recovered_state.status is not SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED
            or recovered_state.adjudication_completed_part_count
            != state.adjudication_completed_part_count
        ):
            raise ThirdAdjudicationSubmissionWorkerError("adjudication replay invalid")
        return _aggregate(
            recovered_state,
            status="submitted",
            estimated=estimate_micros,
            submitted=1,
        )

    secret = read_stable_openai_secret(secret_path)
    before = state
    try:
        returned, updated = _workflow(
            secret, expected_request_sha256=expected_request_hash
        ).submit_next_adjudication(run, verified_run_state=before)
    except RuntimeError as error:
        if contract.is_reconciliation_error(error):
            return _aggregate(before, status="reconciliation_required", estimated=estimate_micros, submitted=0)
        raise ThirdAdjudicationSubmissionWorkerError("adjudication submission failed") from error
    if returned != run or updated.status is not SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication transition invalid")
    if updated.adjudication_completed_part_count != before.adjudication_completed_part_count:
        raise ThirdAdjudicationSubmissionWorkerError("adjudication transition invalid")
    return _aggregate(updated, status="submitted", estimated=estimate_micros, submitted=1)


submit_third_adjudication_part = submit_third_adjudication


def main() -> int:
    try:
        sys.stdout.buffer.write(contract.canonical_json(submit_third_adjudication()))
        return 0
    except Exception:
        sys.stderr.write("error=speaker_review_third_adjudication_submission_failed\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
