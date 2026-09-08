"""Container worker for one authorized primary speaker-review submission.

The worker has no corpus-path CLI.  Root supplies a validated request through
fixed environment variables and mounts only the fixed review-run root.
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
from scripts import private_speaker_review_submission_contract as contract  # noqa: E402

# The mounted workspace preserves the application's expected
# <corpus-root>/review-runs/<run-id> layout so load_validated_run_state can
# enforce the same physical-path confinement used outside the container.
PRIVATE_REVIEW_RUNS_ROOT = Path("/review-workspace/review-runs")
OPENAI_SECRET_PATH = Path("/run/secrets/openai_api_key")
SECRET_MAX_BYTES = 4_096


class SubmissionWorkerError(RuntimeError):
    """Generic worker failure; details never cross the worker boundary."""


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
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        raw = os.read(descriptor, SECRET_MAX_BYTES + 1)
        after = path.lstat()
    except OSError as error:
        raise SubmissionWorkerError("submission secret unavailable") from error
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
        raise SubmissionWorkerError("submission secret unavailable")
    try:
        secret = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SubmissionWorkerError("submission secret unavailable") from error
    if secret.endswith("\n"):
        secret = secret[:-1]
    if not secret or any(character.isspace() for character in secret):
        raise SubmissionWorkerError("submission secret unavailable")
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
        raise SubmissionWorkerError("submission request invalid") from error


def _parse_cost_cap(value: str) -> int:
    try:
        parsed = int(value, 10)
    except (TypeError, ValueError) as error:
        raise SubmissionWorkerError("submission request invalid") from error
    if str(parsed) != value:
        raise SubmissionWorkerError("submission request invalid")
    try:
        return contract.validate_request(
            {
                "archive_sha256": "0" * 64,
                "authorization_id": "00000000-0000-4000-8000-000000000000",
                "maximum_authorized_cost_microusd": parsed,
                "operation": contract.OPERATION,
                "purpose": contract.PURPOSE,
                "run_id": "speaker-review-0000000000000000",
                "schema_version": contract.PROTOCOL_VERSION,
                "season_number": contract.SEASON_NUMBER,
            }
        )["maximum_authorized_cost_microusd"]
    except ValueError as error:
        raise SubmissionWorkerError("submission request invalid") from error


def _cost_microusd(value: float) -> int:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise SubmissionWorkerError("submission cost invalid")
    try:
        micros = Decimal(str(value)) * Decimal(1_000_000)
    except (InvalidOperation, ValueError) as error:
        raise SubmissionWorkerError("submission cost invalid") from error
    if micros != micros.to_integral_value() or micros < 0:
        raise SubmissionWorkerError("submission cost invalid")
    result = int(micros)
    if result > contract.MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise SubmissionWorkerError("submission cost invalid")
    return result


def _aggregate(
    state: SpeakerReviewRunState,
    *,
    status: str,
    submitted_part_count: int,
) -> dict[str, object]:
    result = {
        "estimated_primary_cost_microusd": _cost_microusd(state.estimated_primary_cost_usd),
        "operation": contract.OPERATION,
        "primary_part_count": state.primary_part_count,
        "purpose": contract.PURPOSE,
        "run_id": state.run_id,
        "season_number": contract.SEASON_NUMBER,
        "status": status,
        "submitted_part_count": submitted_part_count,
    }
    try:
        return contract.validate_aggregate(result, status=status)
    except ValueError as error:
        raise SubmissionWorkerError("submission result invalid") from error


def _workflow(secret: str) -> SpeakerReviewGraphWorkflow:
    # The mounted secret is passed directly to the SDK; it is never placed in
    # process arguments, environment output, metadata, or aggregate output.
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


def submit_primary(
    *,
    environment: Mapping[str, str] | None = None,
    secret_path: Path = OPENAI_SECRET_PATH,
    review_root: Path = PRIVATE_REVIEW_RUNS_ROOT,
) -> dict[str, object]:
    request = request_from_environment(environment)
    run_id = str(request["run_id"])
    run_directory = review_root / run_id
    try:
        canonical, state = load_validated_run_state(
            run_directory,
            DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        )
    except Exception as error:
        raise SubmissionWorkerError("submission run unavailable") from error
    if canonical != run_directory:
        raise SubmissionWorkerError("submission run unavailable")
    estimated_micros = _cost_microusd(state.estimated_primary_cost_usd)
    cap = int(request["maximum_authorized_cost_microusd"])
    configuration_cap = _cost_microusd(DEFAULT_SPEAKER_REVIEW_CONFIGURATION.maximum_run_cost_usd)
    if estimated_micros > cap or estimated_micros > configuration_cap:
        raise SubmissionWorkerError("submission cost exceeds authorization")
    if state.status is SpeakerReviewRunStatus.PRIMARY_SUBMITTED:
        return _aggregate(state, status="already_submitted", submitted_part_count=1)
    if state.status is not SpeakerReviewRunStatus.PREPARED:
        raise SubmissionWorkerError("submission run state is not prepared")
    secret = read_stable_openai_secret(secret_path)
    try:
        _, submitted = _workflow(secret).submit(canonical)
    except RuntimeError as error:
        if contract.is_reconciliation_error(error):
            return _aggregate(state, status="reconciliation_required", submitted_part_count=0)
        raise SubmissionWorkerError("primary submission failed") from error
    if submitted.status is not SpeakerReviewRunStatus.PRIMARY_SUBMITTED:
        raise SubmissionWorkerError("primary submission result invalid")
    return _aggregate(submitted, status="submitted", submitted_part_count=1)


def main() -> int:
    try:
        result = submit_primary()
        sys.stdout.buffer.write(contract.canonical_json(result))
        return 0
    except SubmissionWorkerError:
        sys.stderr.write("error=speaker_review_submission_failed\n")
        return 2
    except Exception:
        # Provider and filesystem exception text is deliberately suppressed.
        sys.stderr.write("error=speaker_review_submission_failed\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
