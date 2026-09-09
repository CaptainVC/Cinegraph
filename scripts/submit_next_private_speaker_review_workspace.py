"""Submit at most one next primary speaker-review Batch part.

This is an internal egress-only worker.  It accepts only a run identifier,
requires an explicit ``PRIMARY_PART_COMPLETED`` checkpoint, and never observes
or parses provider output.  The OpenAI secret is read only after all local
checkpoint and cost checks have passed.
"""

from __future__ import annotations

import math
import os
import stat
import sys
from decimal import Decimal, InvalidOperation
from hashlib import sha256
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
from cinegraph.config.speaker_review_filesystem import (  # noqa: E402
    PRIVATE_ARTIFACT_MAX_BYTES,
)
from cinegraph.config.speaker_review_submission import (  # noqa: E402
    SUBMISSION_REQUEST_MAX_BYTES,
    SUBMISSION_SCHEMA_VERSION,
    submission_filename,
)
from cinegraph.domain.enums.enum import SpeakerReviewRunStatus  # noqa: E402
from cinegraph.ingestion.speaker_review.private_io import stable_file_snapshot  # noqa: E402
from cinegraph.ingestion.speaker_review.workflow import (  # noqa: E402
    SpeakerReviewRunState,
    SpeakerReviewWorkflow,
    _part_stage_name,
    _read_submission_record,
    _request_part_path,
    _submission_path,
    load_validated_run_state,
)
from scripts import (  # noqa: E402
    private_speaker_review_next_primary_submission_contract as contract,  # noqa: E402
)

PRIVATE_REVIEW_RUNS_ROOT = Path("/review-workspace/review-runs")
OPENAI_SECRET_PATH = Path("/run/secrets/openai_api_key")
SECRET_MAX_BYTES = 4_096


class NextPrimarySubmissionWorkerError(RuntimeError):
    """Generic worker failure; details never cross the worker boundary."""


def _stable_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_nlink)


def read_stable_openai_secret(path: Path = OPENAI_SECRET_PATH) -> str:
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
        raise NextPrimarySubmissionWorkerError("submission secret unavailable") from error
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
        raise NextPrimarySubmissionWorkerError("submission secret unavailable")
    try:
        secret = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise NextPrimarySubmissionWorkerError("submission secret unavailable") from error
    if secret.endswith("\n"):
        secret = secret[:-1]
    if not secret or any(character.isspace() for character in secret):
        raise NextPrimarySubmissionWorkerError("submission secret unavailable")
    return secret


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
        raise NextPrimarySubmissionWorkerError("submission request invalid") from error


def _parse_cost_cap(value: str) -> int:
    try:
        parsed = int(value, 10)
    except (TypeError, ValueError) as error:
        raise NextPrimarySubmissionWorkerError("submission request invalid") from error
    if str(parsed) != value:
        raise NextPrimarySubmissionWorkerError("submission request invalid")
    try:
        return int(
            contract.validate_request(
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
        )
    except (TypeError, ValueError) as error:
        raise NextPrimarySubmissionWorkerError("submission request invalid") from error


def _cost_microusd(value: float) -> int:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise NextPrimarySubmissionWorkerError("submission cost invalid")
    try:
        micros = Decimal(str(value)) * Decimal(1_000_000)
    except (InvalidOperation, ValueError) as error:
        raise NextPrimarySubmissionWorkerError("submission cost invalid") from error
    if micros != micros.to_integral_value() or micros < 0:
        raise NextPrimarySubmissionWorkerError("submission cost invalid")
    result = int(micros)
    if result > contract.MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise NextPrimarySubmissionWorkerError("submission cost invalid")
    return result


def _run_directory(run_id: str, review_root: Path = PRIVATE_REVIEW_RUNS_ROOT) -> Path:
    try:
        root = review_root.resolve(strict=True)
        candidate = review_root / run_id
        if candidate.resolve(strict=False).parent != root:
            raise NextPrimarySubmissionWorkerError("submission run unavailable")
    except OSError as error:
        raise NextPrimarySubmissionWorkerError("submission run unavailable") from error
    return candidate


def _validate_checkpoint(state: SpeakerReviewRunState) -> None:
    ids = state.primary_batch_ids
    input_ids = state.primary_input_file_ids
    valid_ids = (
        isinstance(ids, tuple)
        and all(isinstance(value, str) and value and value == value.strip() for value in ids)
        and len(set(ids)) == len(ids)
    )
    valid_input_ids = (
        isinstance(input_ids, tuple)
        and all(isinstance(value, str) and value and value == value.strip() for value in input_ids)
        and len(set(input_ids)) == len(input_ids)
    )
    if (
        state.status is not SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED
        or type(state.primary_part_count) is not int
        or state.primary_part_count <= 0
        or type(state.primary_completed_part_count) is not int
        or state.primary_completed_part_count <= 0
        or state.primary_completed_part_count > state.primary_part_count
        or not valid_ids
        or not valid_input_ids
        or len(ids) != state.primary_completed_part_count
        or len(input_ids) != state.primary_completed_part_count
        or state.primary_batch_id != ids[-1]
        or state.primary_input_file_id != input_ids[-1]
    ):
        raise NextPrimarySubmissionWorkerError("submission checkpoint invalid")


def _aggregate(state: SpeakerReviewRunState, *, status: str, submitted: int) -> dict[str, object]:
    result = {
        "estimated_primary_cost_microusd": _cost_microusd(state.estimated_primary_cost_usd),
        "operation": contract.OPERATION,
        "primary_completed_part_count": state.primary_completed_part_count,
        "primary_part_count": state.primary_part_count,
        "purpose": contract.PURPOSE,
        "run_id": state.run_id,
        "season_number": contract.SEASON_NUMBER,
        "status": status,
        "submitted_part_count": submitted,
    }
    try:
        return contract.validate_aggregate(result, status=status)
    except ValueError as error:
        raise NextPrimarySubmissionWorkerError("submission result invalid") from error


def _workflow(secret: str) -> SpeakerReviewGraphWorkflow:
    from openai import OpenAI

    client = OpenAI(api_key=secret)
    models = DEFAULT_MODEL_CONFIGURATION
    review_workflow = SpeakerReviewWorkflow(
        gateway=OpenAISpeakerReviewBatchGateway(client, DEFAULT_SPEAKER_REVIEW_CONFIGURATION),
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        primary_model=models.speaker_review_model,
        adjudication_model=models.speaker_adjudication_model,
        final_review_model=models.speaker_final_review_model,
        primary_reasoning_effort=models.speaker_review_reasoning_effort,
        adjudication_reasoning_effort=models.speaker_adjudication_reasoning_effort,
        final_review_reasoning_effort=models.speaker_final_review_reasoning_effort,
    )
    return SpeakerReviewGraphWorkflow(review_workflow)


def _validate_transition(
    before: SpeakerReviewRunState,
    after: SpeakerReviewRunState,
    returned_directory: Path,
    canonical_directory: Path,
) -> None:
    if (
        not isinstance(after, SpeakerReviewRunState)
        or after.run_id != before.run_id
        or returned_directory != canonical_directory
    ):
        raise NextPrimarySubmissionWorkerError("submission result invalid")
    if after.status is not SpeakerReviewRunStatus.PRIMARY_SUBMITTED:
        raise NextPrimarySubmissionWorkerError("submission result invalid")
    if after.primary_completed_part_count != before.primary_completed_part_count:
        raise NextPrimarySubmissionWorkerError("submission result invalid")
    if (
        not isinstance(after.primary_batch_ids, tuple)
        or not isinstance(after.primary_input_file_ids, tuple)
        or after.primary_batch_ids[:-1] != before.primary_batch_ids
        or after.primary_input_file_ids[:-1] != before.primary_input_file_ids
        or not all(
            isinstance(value, str) and value and value == value.strip()
            for value in (*after.primary_batch_ids[-1:], *after.primary_input_file_ids[-1:])
        )
        or not all(isinstance(value, str) for value in after.primary_batch_ids)
        or not all(isinstance(value, str) for value in after.primary_input_file_ids)
        or len(set(after.primary_batch_ids)) != len(after.primary_batch_ids)
        or len(set(after.primary_input_file_ids)) != len(after.primary_input_file_ids)
    ):
        raise NextPrimarySubmissionWorkerError("submission result invalid")
    if len(after.primary_batch_ids) != len(before.primary_batch_ids) + 1:
        raise NextPrimarySubmissionWorkerError("submission result invalid")
    if len(after.primary_input_file_ids) != len(before.primary_input_file_ids) + 1:
        raise NextPrimarySubmissionWorkerError("submission result invalid")
    if (
        after.primary_batch_id != after.primary_batch_ids[-1]
        or after.primary_input_file_id != after.primary_input_file_ids[-1]
    ):
        raise NextPrimarySubmissionWorkerError("submission result invalid")
    before_payload, after_payload = before.to_dict(), after.to_dict()
    allowed = {
        "status",
        "updated_at",
        "primary_batch_id",
        "primary_input_file_id",
        "primary_batch_ids",
        "primary_input_file_ids",
    }
    if any(before_payload[key] != after_payload[key] for key in before_payload.keys() - allowed):
        raise NextPrimarySubmissionWorkerError("submission result invalid")


def _validate_submitted_replay(state: SpeakerReviewRunState) -> bool:
    """Recognize only a complete post-submit state for this exact operation."""

    if state.status is not SpeakerReviewRunStatus.PRIMARY_SUBMITTED:
        return False
    completed = state.primary_completed_part_count
    parts = state.primary_part_count
    ids, input_ids = state.primary_batch_ids, state.primary_input_file_ids
    valid = (
        type(parts) is int
        and parts > 0
        and type(completed) is int
        and completed > 0
        and completed < parts
        and isinstance(ids, tuple)
        and isinstance(input_ids, tuple)
        and len(ids) == completed + 1
        and len(input_ids) == completed + 1
        and all(isinstance(value, str) and value and value == value.strip() for value in ids)
        and all(isinstance(value, str) and value and value == value.strip() for value in input_ids)
        and len(set(ids)) == len(ids)
        and len(set(input_ids)) == len(input_ids)
        and state.primary_batch_id == ids[-1]
        and state.primary_input_file_id == input_ids[-1]
    )
    if completed == 0 or not valid:
        raise NextPrimarySubmissionWorkerError("submission checkpoint invalid")
    return True


def _validate_next_request_artifact(canonical: Path, completed_count: int) -> None:
    request_path = _request_part_path(canonical, "primary", completed_count)
    try:
        snapshot = stable_file_snapshot(request_path, max_bytes=SUBMISSION_REQUEST_MAX_BYTES)
    except Exception as error:
        raise NextPrimarySubmissionWorkerError("submission request unavailable") from error
    if not snapshot.content:
        raise NextPrimarySubmissionWorkerError("submission request unavailable")


def _validate_checkpoint_evidence(canonical: Path, state: SpeakerReviewRunState) -> None:
    """Prove every completed part has immutable output and matching journals."""

    for index in range(state.primary_completed_part_count):
        try:
            output_path = canonical / f"{_part_stage_name('primary', index)}-output.jsonl"
            output = stable_file_snapshot(output_path, max_bytes=PRIVATE_ARTIFACT_MAX_BYTES)
            if not output.content:
                raise ValueError
            _validate_submission_evidence(canonical, state, index)
        except Exception as error:
            raise NextPrimarySubmissionWorkerError("submission checkpoint evidence invalid") from error


def _validate_submission_evidence(
    canonical: Path,
    state: SpeakerReviewRunState,
    index: int,
) -> None:
    """Validate one exact primary request and its create-once submission records."""

    part = index + 1
    request_path = _request_part_path(canonical, "primary", index)
    request = stable_file_snapshot(request_path, max_bytes=SUBMISSION_REQUEST_MAX_BYTES)
    if not request.content:
        raise ValueError
    intent = _read_submission_record(
        _submission_path(canonical, "primary", index, "intent"), completed=False
    )
    completed = _read_submission_record(
        _submission_path(canonical, "primary", index, "completed"), completed=True
    )
    binding = {
        "schema_version": SUBMISSION_SCHEMA_VERSION,
        "request_sha256": sha256(request.content).hexdigest(),
        "run_id": state.run_id,
        "stage": "primary",
        "part": part,
        "prompt_version": state.prompt_version,
        "batch_endpoint": DEFAULT_SPEAKER_REVIEW_CONFIGURATION.batch_endpoint,
        "completion_window": DEFAULT_SPEAKER_REVIEW_CONFIGURATION.batch_completion_window,
    }
    if (
        intent is None
        or completed is None
        or intent["binding"] != binding
        or completed["binding"] != binding
        or completed["batch_id"] != state.primary_batch_ids[index]
        or completed["input_file_id"] != state.primary_input_file_ids[index]
    ):
        raise ValueError


def _validate_submitted_replay_evidence(
    canonical: Path,
    state: SpeakerReviewRunState,
) -> None:
    """Bind an idempotent replay to all completed and active journals."""

    try:
        _validate_checkpoint_evidence(canonical, state)
        _validate_submission_evidence(
            canonical,
            state,
            state.primary_completed_part_count,
        )
    except Exception as error:
        raise NextPrimarySubmissionWorkerError("submission checkpoint evidence invalid") from error


def submit_next_primary(
    *,
    environment: Mapping[str, str] | None = None,
    secret_path: Path = OPENAI_SECRET_PATH,
    review_root: Path = PRIVATE_REVIEW_RUNS_ROOT,
) -> dict[str, object]:
    request = request_from_environment(environment)
    run_directory = _run_directory(str(request["run_id"]), review_root)
    try:
        canonical, state = load_validated_run_state(
            run_directory, DEFAULT_SPEAKER_REVIEW_CONFIGURATION
        )
    except Exception as error:
        raise NextPrimarySubmissionWorkerError("submission run unavailable") from error
    if canonical != run_directory:
        raise NextPrimarySubmissionWorkerError("submission run unavailable")
    estimated = _cost_microusd(state.estimated_primary_cost_usd)
    cap = int(request["maximum_authorized_cost_microusd"])
    configuration_cap = _cost_microusd(
        DEFAULT_SPEAKER_REVIEW_CONFIGURATION.maximum_run_cost_usd
    )
    if estimated > cap or estimated > configuration_cap:
        raise NextPrimarySubmissionWorkerError("submission cost exceeds authorization")
    if state.status is SpeakerReviewRunStatus.PRIMARY_SUBMITTED:
        _validate_submitted_replay(state)
        _validate_submitted_replay_evidence(canonical, state)
        return _aggregate(state, status="already_submitted", submitted=1)
    _validate_checkpoint(state)
    _validate_checkpoint_evidence(run_directory, state)
    if state.primary_completed_part_count >= state.primary_part_count:
        return _aggregate(state, status="all_parts_completed", submitted=0)

    part = state.primary_completed_part_count + 1
    _validate_next_request_artifact(canonical, state.primary_completed_part_count)
    completed_journal = canonical / submission_filename("primary", part, "completed")
    had_completed_journal = completed_journal.exists()
    secret = read_stable_openai_secret(secret_path)
    before = state
    try:
        returned, submitted = _workflow(secret).submit_next_primary(canonical)
    except RuntimeError as error:
        if contract.is_reconciliation_error(error):
            return _aggregate(before, status="reconciliation_required", submitted=0)
        raise NextPrimarySubmissionWorkerError("next primary submission failed") from error
    _validate_transition(before, submitted, returned, canonical)
    return _aggregate(
        submitted,
        status="already_submitted" if had_completed_journal else "submitted",
        submitted=1,
    )


def main() -> int:
    try:
        result = submit_next_primary()
        sys.stdout.buffer.write(contract.canonical_json(result))
        return 0
    except Exception:
        sys.stderr.write("error=speaker_review_next_primary_submission_failed\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
