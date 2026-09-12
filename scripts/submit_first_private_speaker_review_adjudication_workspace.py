"""Submit exactly adjudication part one for a private speaker-review run.

The worker is intentionally small and egress-only.  It accepts a digest-bound
``ADJUDICATION_PREPARED`` checkpoint, reads the OpenAI key from the Compose
secret file only after local checks, and returns aggregate data without any
provider identifiers.
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
from cinegraph.ingestion.speaker_review.costs import (  # noqa: E402
    estimate_batch_cost_usd,
)
from cinegraph.ingestion.speaker_review.workflow import (  # noqa: E402
    SpeakerReviewRunState,
    SpeakerReviewWorkflow,
    _read_submission_record,
    _request_part_path,
    _submission_path,
    load_validated_run_state,
)
from cinegraph.ports.llm.speaker_review_batch_gateway import (  # noqa: E402
    BatchSnapshot,
    BatchSubmission,
)
from scripts import private_speaker_review_first_adjudication_submission_contract as contract  # noqa: E402

PRIVATE_REVIEW_RUNS_ROOT = Path("/review-workspace/review-runs")
OPENAI_SECRET_PATH = Path("/run/secrets/openai_api_key")
SECRET_MAX_BYTES = 4_096
STATE_MAX_BYTES = 64 * 1024
ARTIFACT_MAX_BYTES = 64 * 1024 * 1024
TOTAL_MAX_BYTES = 256 * 1024 * 1024
STATE = "run-state.json"


class FirstAdjudicationSubmissionWorkerError(RuntimeError):
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
        if (
            _identity(before) != _identity(opened)
            or _identity(opened) != _identity(after)
            or len(raw) != opened.st_size
        ):
            raise OSError
        return raw
    except OSError as error:
        raise FirstAdjudicationSubmissionWorkerError("adjudication evidence unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def read_stable_openai_secret(path: Path = OPENAI_SECRET_PATH) -> str:
    try:
        metadata = path.lstat()
        if stat.S_IMODE(metadata.st_mode) != 0o400 or (
            os.name == "posix"
            and (metadata.st_uid, metadata.st_gid) != (os.geteuid(), os.getegid())
        ):
            raise OSError
    except OSError as error:
        raise FirstAdjudicationSubmissionWorkerError("adjudication secret unavailable") from error
    raw = _stable(path, SECRET_MAX_BYTES)
    try:
        secret = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise FirstAdjudicationSubmissionWorkerError("adjudication secret unavailable") from error
    if secret.endswith("\n"):
        secret = secret[:-1]
    if not secret or any(character.isspace() for character in secret):
        raise FirstAdjudicationSubmissionWorkerError("adjudication secret unavailable")
    return secret


def _parse_cost_cap(value: str) -> int:
    try:
        parsed = int(value, 10)
    except (TypeError, ValueError) as error:
        raise FirstAdjudicationSubmissionWorkerError("adjudication request invalid") from error
    if str(parsed) != value or parsed <= 0 or parsed > contract.MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise FirstAdjudicationSubmissionWorkerError("adjudication request invalid")
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
        raise FirstAdjudicationSubmissionWorkerError("adjudication request invalid") from error


def _run_directory(run_id: str, review_root: Path = PRIVATE_REVIEW_RUNS_ROOT) -> Path:
    try:
        root = review_root.resolve(strict=True)
        candidate = review_root / run_id
        if candidate.resolve(strict=False).parent != root:
            raise OSError
        if not candidate.is_dir() or candidate.is_symlink():
            raise OSError
        return candidate.resolve(strict=True)
    except OSError as error:
        raise FirstAdjudicationSubmissionWorkerError("adjudication run unavailable") from error


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


def _inventory(run: Path) -> tuple[dict[str, bytes], SpeakerReviewRunState]:
    try:
        names = {entry.name for entry in run.iterdir()}
    except OSError as error:
        raise FirstAdjudicationSubmissionWorkerError(
            "adjudication inventory unavailable"
        ) from error
    if STATE not in names:
        raise FirstAdjudicationSubmissionWorkerError("adjudication inventory invalid")
    contents: dict[str, bytes] = {}
    total = 0
    for name in sorted(names):
        raw = _stable(run / name, STATE_MAX_BYTES if name == STATE else ARTIFACT_MAX_BYTES)
        total += len(raw)
        if total > TOTAL_MAX_BYTES:
            raise FirstAdjudicationSubmissionWorkerError("adjudication inventory too large")
        contents[name] = raw
    try:
        state_value = json.loads(contents[STATE].decode("utf-8"))
        canonical, state = load_validated_run_state(run, DEFAULT_SPEAKER_REVIEW_CONFIGURATION)
        if canonical != run or state_value != state.to_dict():
            raise ValueError
    except Exception as error:
        raise FirstAdjudicationSubmissionWorkerError("adjudication run state invalid") from error
    expected = {
        STATE,
        "candidates.jsonl",
        "source-manifest.json",
        *(
            f"primary-part-{index:04d}-requests.jsonl"
            for index in range(1, state.primary_part_count + 1)
        ),
        *(
            f"adjudication-part-{index:04d}-requests.jsonl"
            for index in range(1, state.adjudication_part_count + 1)
        ),
        *(
            f".primary-part-{index:04d}-submission-{kind}.json"
            for index in range(1, state.primary_part_count + 1)
            for kind in ("intent", "completed")
        ),
        *(
            f"primary-part-{index:04d}-output.jsonl"
            for index in range(1, state.primary_part_count + 1)
        ),
        "primary-verdicts.jsonl",
        "primary-parse-errors.json",
        "primary-decisions.jsonl",
    }
    optional = {
        *(
            f"primary-part-{index:04d}-api-errors.jsonl"
            for index in range(1, state.primary_part_count + 1)
        ),
    }
    replay = {
        ".adjudication-part-0001-submission-intent.json",
        ".adjudication-part-0001-submission-completed.json",
    }
    if set(contents) - expected - optional - replay or not expected <= set(contents):
        raise FirstAdjudicationSubmissionWorkerError("adjudication inventory invalid")
    if state.status not in {
        SpeakerReviewRunStatus.ADJUDICATION_PREPARED,
        SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED,
    }:
        raise FirstAdjudicationSubmissionWorkerError("adjudication inventory invalid")
    if state.status is SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED and not replay <= set(
        contents
    ):
        raise FirstAdjudicationSubmissionWorkerError("adjudication inventory invalid")
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
    if any(value == "" for value in expected.values()):
        raise FirstAdjudicationSubmissionWorkerError("adjudication checkpoint binding invalid")
    if any(
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
        for value in expected.values()
    ):
        raise FirstAdjudicationSubmissionWorkerError("adjudication checkpoint binding invalid")
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
        elif (name.startswith("primary-part-") and name.endswith("-requests.jsonl")) or name in {
            "candidates.jsonl",
            "source-manifest.json",
        }:
            artifacts[name] = raw
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
        raise FirstAdjudicationSubmissionWorkerError("adjudication checkpoint changed")


def _parse_requests(
    contents: Mapping[str, bytes], state: SpeakerReviewRunState
) -> tuple[dict[str, object], ...]:
    requests: list[dict[str, object]] = []
    for index in range(state.adjudication_part_count):
        name = f"adjudication-part-{index + 1:04d}-requests.jsonl"
        raw = contents.get(name)
        if raw is None:
            raise FirstAdjudicationSubmissionWorkerError("adjudication request unavailable")
        try:
            for line in raw.splitlines():
                value = json.loads(line.decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError
                requests.append(value)
        except (UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise FirstAdjudicationSubmissionWorkerError("adjudication request invalid") from error
    if not requests:
        raise FirstAdjudicationSubmissionWorkerError("adjudication request unavailable")
    return tuple(requests)


def _expected_request_hash(
    contents: Mapping[str, bytes], environment: Mapping[str, str]
) -> str | None:
    expected = environment.get(contract.ENV_EXPECTED_REQUEST_SHA256, "")
    if (
        not isinstance(expected, str)
        or len(expected) != 64
        or any(c not in "0123456789abcdef" for c in expected)
    ):
        raise FirstAdjudicationSubmissionWorkerError("adjudication checkpoint binding invalid")
    try:
        actual = _sha(contents["adjudication-part-0001-requests.jsonl"])
    except KeyError as error:
        raise FirstAdjudicationSubmissionWorkerError("adjudication request unavailable") from error
    if actual != expected:
        raise FirstAdjudicationSubmissionWorkerError("adjudication checkpoint changed")
    return expected


def _cost_micros(value: float) -> int:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise FirstAdjudicationSubmissionWorkerError("adjudication cost invalid")
    try:
        micros = Decimal(str(value)) * Decimal(1_000_000)
    except (InvalidOperation, ValueError) as error:
        raise FirstAdjudicationSubmissionWorkerError("adjudication cost invalid") from error
    if micros < 0:
        raise FirstAdjudicationSubmissionWorkerError("adjudication cost invalid")
    result = int(micros.to_integral_value(rounding=ROUND_CEILING))
    if result > contract.MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise FirstAdjudicationSubmissionWorkerError("adjudication cost invalid")
    return result


def _aggregate(
    state: SpeakerReviewRunState, *, status: str, estimated: int, submitted: int
) -> dict[str, object]:
    value = {
        "actual_primary_cost_microusd": _cost_micros(state.actual_primary_cost_usd),
        "adjudication_part_count": state.adjudication_part_count,
        "estimated_adjudication_cost_microusd": estimated,
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": state.run_id,
        "run_status": state.status.value,
        "season_number": contract.SEASON_NUMBER,
        "status": status,
        "submitted_part_count": submitted,
    }
    try:
        return contract.validate_aggregate(value, status=status)
    except (TypeError, ValueError) as error:
        raise FirstAdjudicationSubmissionWorkerError("adjudication aggregate invalid") from error


def _validate_replay_evidence(
    run: Path, contents: Mapping[str, bytes], state: SpeakerReviewRunState
) -> None:
    """Validate journals using the application's strict create-once parser."""
    request_name = "adjudication-part-0001-requests.jsonl"
    try:
        request = contents[request_name]
        intent = _read_submission_record(
            _submission_path(run, "adjudication", 0, "intent"), completed=False
        )
        completed = _read_submission_record(
            _submission_path(run, "adjudication", 0, "completed"), completed=True
        )
    except (KeyError, RuntimeError) as error:
        raise FirstAdjudicationSubmissionWorkerError("adjudication evidence invalid") from error
    binding = {
        "schema_version": 1,
        "request_sha256": _sha(request),
        "run_id": state.run_id,
        "stage": "adjudication",
        "part": 1,
        "prompt_version": state.prompt_version,
        "batch_endpoint": DEFAULT_SPEAKER_REVIEW_CONFIGURATION.batch_endpoint,
        "completion_window": DEFAULT_SPEAKER_REVIEW_CONFIGURATION.batch_completion_window,
    }
    if (
        intent is None
        or completed is None
        or intent.get("binding") != binding
        or completed.get("binding") != binding
        or completed.get("batch_id") != state.adjudication_batch_ids[0]
        or completed.get("input_file_id") != state.adjudication_input_file_ids[0]
    ):
        raise FirstAdjudicationSubmissionWorkerError("adjudication evidence invalid")


def _workflow(
    secret: str, *, expected_request_sha256: str | None = None
) -> SpeakerReviewGraphWorkflow:
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
        expected_first_adjudication_request_sha256=expected_request_sha256,
    )
    return SpeakerReviewGraphWorkflow(review)


class _ProviderDisabledGateway:
    """Gateway used by graph replay validation; no provider call is possible."""

    def submit(self, *args: object, **kwargs: object) -> BatchSubmission:
        raise AssertionError("provider access disabled during replay")

    def retrieve(self, *args: object, **kwargs: object) -> BatchSnapshot:
        raise AssertionError("provider access disabled during replay")

    def download_file(self, *args: object, **kwargs: object) -> str:
        raise AssertionError("provider access disabled during replay")


def _replay_workflow() -> SpeakerReviewGraphWorkflow:
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
    )
    return SpeakerReviewGraphWorkflow(review)


def submit_first_adjudication(
    *,
    environment: Mapping[str, str] | None = None,
    secret_path: Path = OPENAI_SECRET_PATH,
    review_root: Path = PRIVATE_REVIEW_RUNS_ROOT,
) -> dict[str, object]:
    values = os.environ if environment is None else environment
    request = request_from_environment(environment)
    run = _run_directory(str(request["run_id"]), review_root)
    try:
        contents, state = _inventory(run)
    except FirstAdjudicationSubmissionWorkerError:
        raise
    # The coordinator binds the exact snapshot being operated on.  This is
    # required for replays too, preventing same-UID file replacement.
    _digest_bindings(contents, values)
    expected_request_hash = _expected_request_hash(contents, values)
    # A replay is checked against its complete submitted state and journals.
    # The coordinator supplies digests for that exact current snapshot, while
    # its create-once intent retains the separately verified prepared hashes.
    if state.status is SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED:
        if (
            state.adjudication_completed_part_count != 0
            or len(state.adjudication_batch_ids) != 1
            or len(state.adjudication_input_file_ids) != 1
            or state.adjudication_batch_id != state.adjudication_batch_ids[0]
            or state.adjudication_input_file_id != state.adjudication_input_file_ids[0]
        ):
            raise FirstAdjudicationSubmissionWorkerError("adjudication checkpoint invalid")
        _validate_replay_evidence(run, contents, state)
        requests = _parse_requests(contents, state)
        try:
            estimate = estimate_batch_cost_usd(
                requests=requests,
                model=DEFAULT_MODEL_CONFIGURATION.speaker_adjudication_model,
                configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
            )
        except Exception as error:
            raise FirstAdjudicationSubmissionWorkerError("adjudication cost invalid") from error
        estimate_micros = _cost_micros(estimate)
        actual_primary = _cost_micros(state.actual_primary_cost_usd)
        cap = int(request["maximum_authorized_cost_microusd"])
        configured = _cost_micros(DEFAULT_SPEAKER_REVIEW_CONFIGURATION.maximum_run_cost_usd)
        if actual_primary + estimate_micros > cap or actual_primary + estimate_micros > configured:
            raise FirstAdjudicationSubmissionWorkerError("adjudication cost exceeds authorization")
        try:
            replay_directory, replay_state = _replay_workflow().submit_first_adjudication(
                run, verified_run_state=state
            )
        except RuntimeError as error:
            raise FirstAdjudicationSubmissionWorkerError("adjudication evidence invalid") from error
        if replay_directory != run or replay_state != state:
            raise FirstAdjudicationSubmissionWorkerError("adjudication replay invalid")
        return _aggregate(state, status="already_submitted", estimated=estimate_micros, submitted=1)

    requests = _parse_requests(contents, state)
    try:
        estimate = estimate_batch_cost_usd(
            requests=requests,
            model=DEFAULT_MODEL_CONFIGURATION.speaker_adjudication_model,
            configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        )
    except Exception as error:
        raise FirstAdjudicationSubmissionWorkerError("adjudication cost invalid") from error
    estimate_micros = _cost_micros(estimate)
    actual_primary = _cost_micros(state.actual_primary_cost_usd)
    cap = int(request["maximum_authorized_cost_microusd"])
    configured = _cost_micros(DEFAULT_SPEAKER_REVIEW_CONFIGURATION.maximum_run_cost_usd)
    if actual_primary + estimate_micros > cap or actual_primary + estimate_micros > configured:
        raise FirstAdjudicationSubmissionWorkerError("adjudication cost exceeds authorization")
    if state.status is not SpeakerReviewRunStatus.ADJUDICATION_PREPARED:
        raise FirstAdjudicationSubmissionWorkerError("adjudication checkpoint invalid")
    # A crash after intent creation is ambiguous.  Resolve it before secret
    # access so retry cannot issue a second provider request.
    intent_name = ".adjudication-part-0001-submission-intent.json"
    completed_name = ".adjudication-part-0001-submission-completed.json"
    if intent_name in contents or completed_name in contents:
        try:
            intent = _read_submission_record(
                _submission_path(run, "adjudication", 0, "intent"), completed=False
            )
            completed = _read_submission_record(
                _submission_path(run, "adjudication", 0, "completed"), completed=True
            )
        except (RuntimeError, OSError) as error:
            raise FirstAdjudicationSubmissionWorkerError("adjudication evidence invalid") from error
        if intent is None or completed is None:
            return _aggregate(
                state, status="reconciliation_required", estimated=estimate_micros, submitted=0
            )
        # Both records mean the provider accepted the request before a crash;
        # let the application graph recover the durable transition without
        # opening the secret or making another provider call.
        try:
            recovered_directory, recovered_state = _replay_workflow().submit_first_adjudication(
                run, verified_run_state=state
            )
        except RuntimeError as error:
            raise FirstAdjudicationSubmissionWorkerError("adjudication evidence invalid") from error
        if (
            recovered_directory != run
            or recovered_state.status is not SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED
        ):
            raise FirstAdjudicationSubmissionWorkerError("adjudication replay invalid")
        return _aggregate(
            recovered_state, status="submitted", estimated=estimate_micros, submitted=1
        )
    first_name = _request_part_path(run, "adjudication", 0).name
    first_hash = _sha(contents[first_name])
    if expected_request_hash is not None and first_hash != expected_request_hash:
        raise FirstAdjudicationSubmissionWorkerError("adjudication checkpoint changed")
    secret = read_stable_openai_secret(secret_path)
    before = state
    try:
        returned, updated = _workflow(
            secret, expected_request_sha256=first_hash
        ).submit_first_adjudication(run, verified_run_state=before)
    except RuntimeError as error:
        if contract.is_reconciliation_error(error):
            return _aggregate(
                before, status="reconciliation_required", estimated=estimate_micros, submitted=0
            )
        raise FirstAdjudicationSubmissionWorkerError("adjudication submission failed") from error
    if returned != run or updated.status is not SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED:
        raise FirstAdjudicationSubmissionWorkerError("adjudication transition invalid")
    return _aggregate(updated, status="submitted", estimated=estimate_micros, submitted=1)


def main() -> int:
    try:
        sys.stdout.buffer.write(contract.canonical_json(submit_first_adjudication()))
        return 0
    except Exception:
        sys.stderr.write("error=speaker_review_first_adjudication_submission_failed\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
