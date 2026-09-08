"""Observe one primary Batch for a prepared private speaker-review run.

This worker performs one read-only provider observation. It never submits a
Batch, advances the workflow, polls in a loop, adjudicates, or finalizes a run.
The root host supplies the digest-bound ``review-runs`` mount and exact request
environment; only the OpenAI secret is read from a Compose secret file.
"""

from __future__ import annotations

import math
import os
import stat
import sys
from decimal import Decimal, InvalidOperation
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
from cinegraph.ingestion.speaker_review.workflow import (  # noqa: E402
    SpeakerReviewRunState,
    SpeakerReviewWorkflow,
    load_validated_run_state,
)
from scripts import private_speaker_review_observation_contract as contract  # noqa: E402

PRIVATE_REVIEW_RUNS_ROOT = Path("/review-workspace/review-runs")
OPENAI_SECRET_PATH = Path("/run/secrets/openai_api_key")
SECRET_MAX_BYTES = 4_096


class ObservationWorkerError(RuntimeError):
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
        raise ObservationWorkerError("observation secret unavailable") from error
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
        raise ObservationWorkerError("observation secret unavailable")
    try:
        secret = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ObservationWorkerError("observation secret unavailable") from error
    if secret.endswith("\n"):
        secret = secret[:-1]
    if not secret or any(character.isspace() for character in secret):
        raise ObservationWorkerError("observation secret unavailable")
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
        raise ObservationWorkerError("observation request invalid") from error


def _parse_cost_cap(value: str) -> int:
    try:
        parsed = int(value, 10)
    except (TypeError, ValueError) as error:
        raise ObservationWorkerError("observation request invalid") from error
    if str(parsed) != value:
        raise ObservationWorkerError("observation request invalid")
    if type(parsed) is not int or parsed <= 0 or parsed > contract.MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise ObservationWorkerError("observation request invalid")
    return parsed


def _cost_microusd(value: float) -> int:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ObservationWorkerError("observation cost invalid")
    try:
        micros = Decimal(str(value)) * Decimal(1_000_000)
    except (InvalidOperation, ValueError) as error:
        raise ObservationWorkerError("observation cost invalid") from error
    if micros != micros.to_integral_value() or micros < 0:
        raise ObservationWorkerError("observation cost invalid")
    result = int(micros)
    if result > contract.MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise ObservationWorkerError("observation cost invalid")
    return result


def _run_directory(run_id: str, review_root: Path = PRIVATE_REVIEW_RUNS_ROOT) -> Path:
    root = review_root.resolve(strict=True)
    candidate = review_root / run_id
    try:
        if candidate.resolve(strict=False).parent != root:
            raise ObservationWorkerError("observation run unavailable")
    except OSError as error:
        raise ObservationWorkerError("observation run unavailable") from error
    return candidate


def _aggregate(
    state: SpeakerReviewRunState,
    *,
    status: str,
) -> dict[str, object]:
    result = {
        "estimated_primary_cost_microusd": _cost_microusd(state.estimated_primary_cost_usd),
        "operation": contract.OPERATION,
        "primary_completed_part_count": state.primary_completed_part_count,
        "primary_part_count": state.primary_part_count,
        "purpose": contract.PURPOSE,
        "run_id": state.run_id,
        "run_status": state.status.value,
        "season_number": contract.SEASON_NUMBER,
        "status": status,
    }
    try:
        return contract.validate_aggregate(result, status=status)
    except ValueError as error:
        raise ObservationWorkerError("observation result invalid") from error


def _validate_state_transition(
    before: SpeakerReviewRunState,
    after: SpeakerReviewRunState,
    returned_directory: Path,
    canonical_directory: Path,
) -> None:
    """Allow only the state mutation owned by one primary observation."""

    if (
        not isinstance(returned_directory, Path)
        or returned_directory != canonical_directory
        or not isinstance(after, SpeakerReviewRunState)
    ):
        raise ObservationWorkerError("observation result invalid")
    before_payload = before.to_dict()
    after_payload = after.to_dict()
    if before.run_id != after.run_id:
        raise ObservationWorkerError("observation result invalid")

    if after.status is SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED:
        if before.status is not SpeakerReviewRunStatus.PRIMARY_SUBMITTED:
            raise ObservationWorkerError("observation result invalid")
        if after.primary_completed_part_count != before.primary_completed_part_count + 1:
            raise ObservationWorkerError("observation result invalid")
        allowed = {"status", "updated_at", "primary_completed_part_count"}
    elif after.status is SpeakerReviewRunStatus.FAILED:
        if before.status is not SpeakerReviewRunStatus.PRIMARY_SUBMITTED:
            raise ObservationWorkerError("observation result invalid")
        if after.primary_completed_part_count != before.primary_completed_part_count:
            raise ObservationWorkerError("observation result invalid")
        allowed = {"status", "updated_at"}
    elif after.status is SpeakerReviewRunStatus.PRIMARY_SUBMITTED:
        if before.status is not SpeakerReviewRunStatus.PRIMARY_SUBMITTED:
            raise ObservationWorkerError("observation result invalid")
        allowed = set()
    else:
        raise ObservationWorkerError("observation result invalid")
    if any(
        before_payload[field] != after_payload[field] for field in before_payload.keys() - allowed
    ):
        raise ObservationWorkerError("observation result invalid")


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


def observe_primary(
    *,
    environment: Mapping[str, str] | None = None,
    secret_path: Path = OPENAI_SECRET_PATH,
    review_root: Path = PRIVATE_REVIEW_RUNS_ROOT,
) -> dict[str, object]:
    request = request_from_environment(environment)
    run_directory = _run_directory(str(request["run_id"]), review_root)
    try:
        canonical, state = load_validated_run_state(
            run_directory,
            DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        )
    except Exception as error:
        raise ObservationWorkerError("observation run unavailable") from error
    if canonical != run_directory:
        raise ObservationWorkerError("observation run unavailable")
    estimated_micros = _cost_microusd(state.estimated_primary_cost_usd)
    cap = int(request["maximum_authorized_cost_microusd"])
    configuration_cap = _cost_microusd(DEFAULT_SPEAKER_REVIEW_CONFIGURATION.maximum_run_cost_usd)
    if estimated_micros > cap or estimated_micros > configuration_cap:
        raise ObservationWorkerError("observation cost exceeds authorization")
    # A completed primary part is the only safe replay state for this
    # operation.  Other states may represent an unrelated stage (or a
    # partially-corrupt run) and must not be reported as already observed.
    if state.status is SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED:
        return _aggregate(state, status="already_observed")
    if state.status is not SpeakerReviewRunStatus.PRIMARY_SUBMITTED:
        raise ObservationWorkerError("observation run state invalid")
    secret = read_stable_openai_secret(secret_path)
    before = state
    try:
        returned_directory, observed = _workflow(secret).observe_primary(canonical)
    except RuntimeError as error:
        if contract.is_reconciliation_error(error):
            return _aggregate(before, status="reconciliation_required")
        raise ObservationWorkerError("primary observation failed") from error
    _validate_state_transition(before, observed, returned_directory, canonical)
    if observed.status is SpeakerReviewRunStatus.FAILED:
        status = "failed"
    elif observed != before:
        status = "observed"
    else:
        status = "waiting"
    return _aggregate(observed, status=status)


def main() -> int:
    try:
        result = observe_primary()
        sys.stdout.buffer.write(contract.canonical_json(result))
        return 0
    except ObservationWorkerError:
        sys.stderr.write("error=speaker_review_observation_failed\n")
        return 2
    except Exception:
        sys.stderr.write("error=speaker_review_observation_failed\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
