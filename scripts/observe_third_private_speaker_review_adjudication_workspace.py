"""Observe exactly adjudication part three for a private review run.

This worker performs one read-only provider observation. It never submits a
Batch, advances the workflow, polls in a loop, adjudicates, or finalizes a run.
The root host supplies the digest-bound ``review-runs`` mount and exact request
environment; only the OpenAI secret is read from a Compose secret file.
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
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping

_ROOT = Path(__file__).resolve().parents[1]
for _import_root in (_ROOT, _ROOT / "src"):
    if os.fspath(_import_root) not in sys.path:
        sys.path.insert(0, os.fspath(_import_root))

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
    load_validated_run_state,
)
from cinegraph.ports.llm.speaker_review_batch_gateway import (  # noqa: E402
    BatchSnapshot,
    BatchSubmission,
)
from scripts import (
    private_speaker_review_third_adjudication_observation_contract as contract,  # noqa: E402
)

PRIVATE_REVIEW_RUNS_ROOT = Path("/review-workspace/review-runs")
OPENAI_SECRET_PATH = Path("/run/secrets/openai_api_key")
SECRET_MAX_BYTES = 4_096


class ThirdAdjudicationObservationWorkerError(RuntimeError):
    """Generic worker failure whose details never cross the boundary."""


def _stable_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_nlink,
    )


def read_stable_openai_secret(path: Path = OPENAI_SECRET_PATH) -> str:
    """Read the mounted secret once, rejecting links, mutation, and unsafe mode."""

    descriptor = -1
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > SECRET_MAX_BYTES
            or stat.S_IMODE(before.st_mode) & 0o077
            or (
                os.name == "posix"
                and (before.st_uid != os.geteuid() or before.st_gid != os.getegid())
            )
        ):
            raise OSError
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        raw = os.read(descriptor, SECRET_MAX_BYTES + 1)
        after = path.lstat()
    except OSError as error:
        raise ThirdAdjudicationObservationWorkerError("observation secret unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        _stable_identity(before) != _stable_identity(opened)
        or _stable_identity(opened) != _stable_identity(after)
        or len(raw) != opened.st_size
        or not raw
        or len(raw) > SECRET_MAX_BYTES
    ):
        raise ThirdAdjudicationObservationWorkerError("observation secret unavailable")
    try:
        secret = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ThirdAdjudicationObservationWorkerError("observation secret unavailable") from error
    if secret.endswith("\n"):
        secret = secret[:-1]
    if not secret or any(character.isspace() for character in secret):
        raise ThirdAdjudicationObservationWorkerError("observation secret unavailable")
    return secret


def request_from_environment(environment: Mapping[str, str] | None = None) -> dict[str, object]:
    values = os.environ if environment is None else environment
    raw: dict[str, object] = {
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
    try:
        return contract.validate_request(raw)
    except (TypeError, ValueError) as error:
        raise ThirdAdjudicationObservationWorkerError("observation request invalid") from error


def _parse_cost_cap(value: str) -> int:
    try:
        parsed = int(value, 10)
    except (TypeError, ValueError) as error:
        raise ThirdAdjudicationObservationWorkerError("observation request invalid") from error
    if str(parsed) != value:
        raise ThirdAdjudicationObservationWorkerError("observation request invalid")
    if type(parsed) is not int or parsed <= 0 or parsed > contract.MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise ThirdAdjudicationObservationWorkerError("observation request invalid")
    return parsed


def _cost_microusd(value: float) -> int:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ThirdAdjudicationObservationWorkerError("observation cost invalid")
    try:
        micros = Decimal(str(value)) * Decimal(1_000_000)
    except (InvalidOperation, ValueError) as error:
        raise ThirdAdjudicationObservationWorkerError("observation cost invalid") from error
    if micros != micros.to_integral_value() or micros < 0:
        raise ThirdAdjudicationObservationWorkerError("observation cost invalid")
    result = int(micros)
    if result > contract.MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise ThirdAdjudicationObservationWorkerError("observation cost invalid")
    return result


def _estimated_adjudication_cost_microusd(
    run_directory: Path,
    state: SpeakerReviewRunState,
) -> int:
    requests: list[dict[str, object]] = []
    try:
        for part in range(1, state.adjudication_part_count + 1):
            raw = _read_checkpoint_file(
                run_directory / f"adjudication-part-{part:04d}-requests.jsonl",
                64 * 1024 * 1024,
            )
            for line in raw.splitlines():
                value = json.loads(
                    line.decode("utf-8"),
                    object_pairs_hook=contract._pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                )
                if not isinstance(value, dict):
                    raise ValueError
                requests.append(value)
        if not requests:
            raise ValueError
        estimate = estimate_batch_cost_usd(
            requests=tuple(requests),
            model=DEFAULT_MODEL_CONFIGURATION.speaker_adjudication_model,
            configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        )
        micros = Decimal(str(estimate)) * Decimal(1_000_000)
        if not micros.is_finite() or micros < 0:
            raise ValueError
        result = int(micros.to_integral_value(rounding=ROUND_CEILING))
        if result > contract.MAXIMUM_AUTHORIZED_COST_MICROUSD:
            raise ValueError
        return result
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, InvalidOperation) as error:
        raise ThirdAdjudicationObservationWorkerError("observation cost invalid") from error


def _run_directory(run_id: str, review_root: Path = PRIVATE_REVIEW_RUNS_ROOT) -> Path:
    root = review_root.resolve(strict=True)
    candidate = review_root / run_id
    try:
        if candidate.resolve(strict=False).parent != root:
            raise ThirdAdjudicationObservationWorkerError("observation run unavailable")
    except OSError as error:
        raise ThirdAdjudicationObservationWorkerError("observation run unavailable") from error
    return candidate


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _set_digest(contents: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(contents):
        encoded = name.encode("ascii")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(contents[name]).to_bytes(8, "big"))
        digest.update(contents[name])
    return digest.hexdigest()


def _read_checkpoint_file(path: Path, maximum: int) -> bytes:
    descriptor = -1
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > maximum
            or (
                os.name == "posix"
                and (
                    stat.S_IMODE(before.st_mode) != 0o600
                    or before.st_uid != os.geteuid()
                    or before.st_gid != os.getegid()
                )
            )
        ):
            raise OSError
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        raw = os.read(descriptor, maximum + 1)
        after = path.lstat()
    except OSError as error:
        raise ThirdAdjudicationObservationWorkerError(
            "observation checkpoint unavailable"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    def identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_nlink,
        )

    if (
        identity(before) != identity(opened)
        or identity(opened) != identity(after)
        or len(raw) != opened.st_size
    ):
        raise ThirdAdjudicationObservationWorkerError("observation checkpoint changed")
    return raw


def _expected_hash(value: str, label: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ThirdAdjudicationObservationWorkerError(f"observation {label} invalid")
    return value


def _expected_inventory_names(
    state: SpeakerReviewRunState,
    *,
    observed: bool,
) -> tuple[set[str], set[str]]:
    completed = state.adjudication_completed_part_count
    journal_end = (
        completed + 1
        if state.status
        in {
            SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED,
            SpeakerReviewRunStatus.FAILED,
        }
        else completed
    )
    required = {
        "candidates.jsonl",
        "source-manifest.json",
        "run-state.json",
        *(
            f"primary-part-{part:04d}-requests.jsonl"
            for part in range(1, state.primary_part_count + 1)
        ),
        *(
            f".primary-part-{part:04d}-submission-intent.json"
            for part in range(1, state.primary_part_count + 1)
        ),
        *(
            f".primary-part-{part:04d}-submission-completed.json"
            for part in range(1, state.primary_part_count + 1)
        ),
        *(
            f"primary-part-{part:04d}-output.jsonl"
            for part in range(1, state.primary_part_count + 1)
        ),
        *(
            f"adjudication-part-{part:04d}-requests.jsonl"
            for part in range(1, state.adjudication_part_count + 1)
        ),
        *(
            f".adjudication-part-{part:04d}-submission-intent.json"
            for part in range(1, journal_end + 1)
        ),
        *(
            f".adjudication-part-{part:04d}-submission-completed.json"
            for part in range(1, journal_end + 1)
        ),
        "primary-verdicts.jsonl",
        "primary-parse-errors.json",
        "primary-decisions.jsonl",
    }
    optional = {
        *(
            f"primary-part-{part:04d}-api-errors.jsonl"
            for part in range(1, state.primary_part_count + 1)
        ),
    }
    if state.status is SpeakerReviewRunStatus.FAILED:
        optional.add("terminal-api-errors.jsonl")
    if observed:
        required.update(
            f"adjudication-part-{part:04d}-output.jsonl" for part in range(1, completed + 1)
        )
        optional.update(
            f"adjudication-part-{part:04d}-api-errors.jsonl" for part in range(1, completed + 1)
        )
    else:
        required.update(
            f"adjudication-part-{part:04d}-output.jsonl" for part in range(1, completed + 1)
        )
        optional.update(
            f"adjudication-part-{part:04d}-api-errors.jsonl" for part in range(1, completed + 1)
        )
    return required, optional


def _validate_expected_checkpoint(
    run_directory: Path,
    state: SpeakerReviewRunState,
    environment: Mapping[str, str],
) -> None:
    """Validate every root-bound pre-observation digest before secret access."""

    expected = {
        "state": environment.get(contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256),
        "artifacts": environment.get(contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256),
        "journals": environment.get(contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256),
        "outputs": environment.get(contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256),
        "derived": environment.get(contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256),
        "request": environment.get(contract.ENV_EXPECTED_REQUEST_SHA256),
    }
    if any(value is None for value in expected.values()):
        raise ThirdAdjudicationObservationWorkerError("observation checkpoint invalid")
    if (
        state.status is not SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED
        or state.adjudication_completed_part_count != contract.PREDECESSOR_COMPLETED_PART_COUNT
        or state.adjudication_part_count <= 0
        or state.adjudication_part_count < contract.OBSERVED_PART_NUMBER
        or state.adjudication_completed_part_count >= state.adjudication_part_count
        or len(state.adjudication_batch_ids) != contract.OBSERVED_PART_NUMBER
        or len(state.adjudication_input_file_ids) != contract.OBSERVED_PART_NUMBER
        or state.adjudication_batch_id != state.adjudication_batch_ids[-1]
        or state.adjudication_input_file_id != state.adjudication_input_file_ids[-1]
    ):
        raise ThirdAdjudicationObservationWorkerError("observation checkpoint invalid")
    if _sha256(
        _read_checkpoint_file(run_directory / "run-state.json", 64 * 1024)
    ) != _expected_hash(str(expected["state"]), "state hash"):
        raise ThirdAdjudicationObservationWorkerError("observation checkpoint changed")
    request_name = (
        f"adjudication-part-{state.adjudication_completed_part_count + 1:04d}-requests.jsonl"
    )
    if _sha256(
        _read_checkpoint_file(run_directory / request_name, 64 * 1024 * 1024)
    ) != _expected_hash(str(expected["request"]), "request hash"):
        raise ThirdAdjudicationObservationWorkerError("observation checkpoint changed")
    try:
        names = {entry.name for entry in run_directory.iterdir()}
    except OSError as error:
        raise ThirdAdjudicationObservationWorkerError(
            "observation checkpoint unavailable"
        ) from error
    required_names, optional_names = _expected_inventory_names(state, observed=False)
    if not required_names <= names or not names <= required_names | optional_names:
        raise ThirdAdjudicationObservationWorkerError("observation checkpoint changed")
    contents: dict[str, bytes] = {}
    total = 0
    for name in sorted(names):
        raw = _read_checkpoint_file(
            run_directory / name,
            64 * 1024 if name == "run-state.json" else 64 * 1024 * 1024,
        )
        total += len(raw)
        if total > 256 * 1024 * 1024:
            raise ThirdAdjudicationObservationWorkerError("observation checkpoint unavailable")
        contents[name] = raw
    artifacts: dict[str, bytes] = {}
    journals: dict[str, bytes] = {}
    outputs: dict[str, bytes] = {}
    derived: dict[str, bytes] = {}
    for name, raw in contents.items():
        if name == "run-state.json":
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
    actual = {
        "artifacts": _set_digest(artifacts),
        "journals": _set_digest(journals),
        "outputs": _set_digest(outputs),
        "derived": _set_digest(derived),
    }
    if any(
        actual[label] != _expected_hash(str(expected[label]), f"{label} hash") for label in actual
    ):
        raise ThirdAdjudicationObservationWorkerError("observation checkpoint changed")


def _checkpoint_binding_present(environment: Mapping[str, str]) -> bool:
    names = (
        contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256,
        contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256,
        contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256,
        contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256,
        contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256,
        contract.ENV_EXPECTED_REQUEST_SHA256,
    )
    present = tuple(environment.get(name) is not None for name in names)
    if any(present) and not all(present):
        raise ThirdAdjudicationObservationWorkerError("observation checkpoint invalid")
    if all(present):
        for name in names:
            _expected_hash(environment[name], "checkpoint")
    return all(present)


def _aggregate(
    state: SpeakerReviewRunState,
    *,
    status: str,
    estimated_adjudication_cost_microusd: int,
) -> dict[str, object]:
    result = {
        "actual_primary_cost_microusd": _cost_microusd(state.actual_primary_cost_usd),
        "adjudication_completed_part_count": state.adjudication_completed_part_count,
        "adjudication_part_count": state.adjudication_part_count,
        "estimated_adjudication_cost_microusd": estimated_adjudication_cost_microusd,
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": state.run_id,
        "run_status": state.status.value,
        "season_number": contract.SEASON_NUMBER,
        "status": status,
    }
    try:
        return contract.validate_aggregate(result, status=status)
    except ValueError as error:
        raise ThirdAdjudicationObservationWorkerError("observation result invalid") from error


def _validate_state_transition(
    before: SpeakerReviewRunState,
    after: SpeakerReviewRunState,
    returned_directory: Path,
    canonical_directory: Path,
) -> None:
    """Allow only the state mutation owned by one next-part observation."""

    if (
        not isinstance(returned_directory, Path)
        or returned_directory != canonical_directory
        or not isinstance(after, SpeakerReviewRunState)
    ):
        raise ThirdAdjudicationObservationWorkerError("observation result invalid")
    before_payload = before.to_dict()
    after_payload = after.to_dict()
    if before.run_id != after.run_id:
        raise ThirdAdjudicationObservationWorkerError("observation result invalid")

    if after.status is SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED:
        if before.status is not SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED:
            raise ThirdAdjudicationObservationWorkerError("observation result invalid")
        if before.adjudication_completed_part_count != contract.PREDECESSOR_COMPLETED_PART_COUNT:
            raise ThirdAdjudicationObservationWorkerError("observation result invalid")
        if after.adjudication_completed_part_count != contract.OBSERVED_PART_NUMBER:
            raise ThirdAdjudicationObservationWorkerError("observation result invalid")
        if after.adjudication_completed_part_count != before.adjudication_completed_part_count + 1:
            raise ThirdAdjudicationObservationWorkerError("observation result invalid")
        allowed = {"status", "updated_at", "adjudication_completed_part_count"}
    elif after.status is SpeakerReviewRunStatus.FAILED:
        if before.status is not SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED:
            raise ThirdAdjudicationObservationWorkerError("observation result invalid")
        if before.adjudication_completed_part_count != contract.PREDECESSOR_COMPLETED_PART_COUNT:
            raise ThirdAdjudicationObservationWorkerError("observation result invalid")
        if after.adjudication_completed_part_count != before.adjudication_completed_part_count:
            raise ThirdAdjudicationObservationWorkerError("observation result invalid")
        allowed = {"status", "updated_at"}
    elif after.status is SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED:
        if before.status is not SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED:
            raise ThirdAdjudicationObservationWorkerError("observation result invalid")
        if before.adjudication_completed_part_count != contract.PREDECESSOR_COMPLETED_PART_COUNT:
            raise ThirdAdjudicationObservationWorkerError("observation result invalid")
        allowed = set()
    else:
        raise ThirdAdjudicationObservationWorkerError("observation result invalid")
    if any(
        before_payload[field] != after_payload[field] for field in before_payload.keys() - allowed
    ):
        raise ThirdAdjudicationObservationWorkerError("observation result invalid")


def _workflow(secret: str) -> SpeakerReviewGraphWorkflow:
    from openai import OpenAI

    client = OpenAI(api_key=secret)
    models = DEFAULT_MODEL_CONFIGURATION
    review_workflow = SpeakerReviewWorkflow(
        gateway=OpenAISpeakerReviewBatchGateway(
            client,
            DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        ),
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        primary_model=models.speaker_review_model,
        adjudication_model=models.speaker_adjudication_model,
        final_review_model=models.speaker_final_review_model,
        primary_reasoning_effort=models.speaker_review_reasoning_effort,
        adjudication_reasoning_effort=models.speaker_adjudication_reasoning_effort,
        final_review_reasoning_effort=models.speaker_final_review_reasoning_effort,
    )
    return SpeakerReviewGraphWorkflow(review_workflow)


class _ProviderDisabledGateway:
    """Make provider access structurally impossible during completed replay."""

    def submit(self, *args: object, **kwargs: object) -> BatchSubmission:
        raise AssertionError("provider access disabled during replay")

    def retrieve(self, *args: object, **kwargs: object) -> BatchSnapshot:
        raise AssertionError("provider access disabled during replay")

    def download_file(self, *args: object, **kwargs: object) -> str:
        raise AssertionError("provider access disabled during replay")


def _replay_workflow() -> SpeakerReviewGraphWorkflow:
    models = DEFAULT_MODEL_CONFIGURATION
    review_workflow = SpeakerReviewWorkflow(
        gateway=_ProviderDisabledGateway(),
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        primary_model=models.speaker_review_model,
        adjudication_model=models.speaker_adjudication_model,
        final_review_model=models.speaker_final_review_model,
        primary_reasoning_effort=models.speaker_review_reasoning_effort,
        adjudication_reasoning_effort=models.speaker_adjudication_reasoning_effort,
        final_review_reasoning_effort=models.speaker_final_review_reasoning_effort,
    )
    return SpeakerReviewGraphWorkflow(review_workflow)


def observe_third_adjudication(
    *,
    environment: Mapping[str, str] | None = None,
    secret_path: Path = OPENAI_SECRET_PATH,
    review_root: Path = PRIVATE_REVIEW_RUNS_ROOT,
) -> dict[str, object]:
    values = os.environ if environment is None else environment
    request = request_from_environment(environment)
    checkpoint_bound = _checkpoint_binding_present(values)
    run_directory = _run_directory(str(request["run_id"]), review_root)
    try:
        canonical, state = load_validated_run_state(
            run_directory,
            DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        )
    except Exception as error:
        raise ThirdAdjudicationObservationWorkerError("observation run unavailable") from error
    if canonical != run_directory:
        raise ThirdAdjudicationObservationWorkerError("observation run unavailable")
    if state.status is SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED:
        if state.adjudication_completed_part_count != contract.PREDECESSOR_COMPLETED_PART_COUNT:
            raise ThirdAdjudicationObservationWorkerError("observation run state invalid")
        _validate_expected_checkpoint(canonical, state, values)
    elif state.status is not SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED:
        raise ThirdAdjudicationObservationWorkerError("observation run state invalid")
    elif state.adjudication_completed_part_count != contract.OBSERVED_PART_NUMBER:
        raise ThirdAdjudicationObservationWorkerError("observation run state invalid")
    actual_primary_micros = _cost_microusd(state.actual_primary_cost_usd)
    estimated_adjudication_micros = _estimated_adjudication_cost_microusd(
        canonical,
        state,
    )
    cap = int(request["maximum_authorized_cost_microusd"])
    configuration_cap = _cost_microusd(DEFAULT_SPEAKER_REVIEW_CONFIGURATION.maximum_run_cost_usd)
    expected_total = actual_primary_micros + estimated_adjudication_micros
    if expected_total > cap or expected_total > configuration_cap:
        raise ThirdAdjudicationObservationWorkerError("observation cost exceeds authorization")
    # A completed third adjudication part is the only safe replay state for this
    # operation.  Other states may represent an unrelated stage (or a
    # partially-corrupt run) and must not be reported as already observed.
    if state.status is SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED:
        required, optional = _expected_inventory_names(state, observed=True)
        try:
            names = {entry.name for entry in canonical.iterdir()}
        except OSError as error:
            raise ThirdAdjudicationObservationWorkerError("observation run unavailable") from error
        if not required <= names or not names <= required | optional:
            raise ThirdAdjudicationObservationWorkerError("observation run state invalid")
        total = 0
        for name in sorted(names):
            raw = _read_checkpoint_file(
                canonical / name,
                64 * 1024 if name == "run-state.json" else 64 * 1024 * 1024,
            )
            total += len(raw)
            if total > 256 * 1024 * 1024:
                raise ThirdAdjudicationObservationWorkerError("observation run unavailable")
        try:
            # The generic workflow primitive is deliberately reused only
            # after this worker pins the Phase 74 request, checkpoint, and
            # part-three inventory contract above.
            returned_directory, replayed = _replay_workflow().observe_next_adjudication(
                canonical,
                verified_run_state=state,
            )
        except (AssertionError, RuntimeError) as error:
            raise ThirdAdjudicationObservationWorkerError("observation replay invalid") from error
        if returned_directory != canonical or replayed != state:
            raise ThirdAdjudicationObservationWorkerError("observation replay invalid")
        return _aggregate(
            state,
            status="already_observed",
            estimated_adjudication_cost_microusd=estimated_adjudication_micros,
        )
    secret = read_stable_openai_secret(secret_path)
    before = state
    try:
        graph = _workflow(secret)
        if checkpoint_bound:
            returned_directory, observed = graph.observe_next_adjudication(
                canonical,
                verified_run_state=before,
            )
        else:
            returned_directory, observed = graph.observe_next_adjudication(canonical)
    except RuntimeError as error:
        if contract.is_reconciliation_error(error):
            return _aggregate(
                before,
                status="reconciliation_required",
                estimated_adjudication_cost_microusd=estimated_adjudication_micros,
            )
        raise ThirdAdjudicationObservationWorkerError(
            "third adjudication observation failed"
        ) from error
    _validate_state_transition(before, observed, returned_directory, canonical)
    if observed.status is SpeakerReviewRunStatus.FAILED:
        status = "failed"
    elif observed != before:
        status = "observed"
    else:
        status = "waiting"
    return _aggregate(
        observed,
        status=status,
        estimated_adjudication_cost_microusd=estimated_adjudication_micros,
    )


def main() -> int:
    try:
        result = observe_third_adjudication()
        sys.stdout.buffer.write(contract.canonical_json(result))
        return 0
    except ThirdAdjudicationObservationWorkerError:
        sys.stderr.write("error=speaker_review_third_adjudication_observation_failed\n")
        return 2
    except Exception:
        sys.stderr.write("error=speaker_review_third_adjudication_observation_failed\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
