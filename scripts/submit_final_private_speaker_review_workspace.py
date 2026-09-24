"""Isolated egress worker for exactly final-review part one.

The worker is intentionally narrow: it accepts only a Phase 78
``FINAL_REVIEW_PREPARED`` checkpoint (or an exact ``FINAL_REVIEW_SUBMITTED``
replay), submits one request part, and exposes only a small aggregate.
"""

from __future__ import annotations

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
for _path in (_ROOT, _ROOT / "src"):
    if os.fspath(_path) not in sys.path:
        sys.path.insert(0, os.fspath(_path))

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
from scripts import (  # noqa: E402
    private_speaker_review_final_review_submission_contract as contract,  # noqa: E402
)

PRIVATE_REVIEW_RUNS_ROOT = Path("/review-workspace/review-runs")
OPENAI_SECRET_PATH = Path("/run/secrets/openai_api_key")
SECRET_MAX_BYTES = 4_096
STATE_MAX_BYTES = 64 * 1024
ARTIFACT_MAX_BYTES = 64 * 1024 * 1024
TOTAL_MAX_BYTES = 256 * 1024 * 1024
STATE = "run-state.json"


class FinalReviewSubmissionWorkerError(RuntimeError):
    """Generic failure that never contains private evidence."""


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


def _effective_owner() -> tuple[int, int] | None:
    uid = getattr(os, "geteuid", None)
    gid = getattr(os, "getegid", None)
    if callable(uid) and callable(gid):
        return int(uid()), int(gid())
    return None


def _validate_file_metadata(value: os.stat_result) -> None:
    owner = _effective_owner()
    if (
        not stat.S_ISREG(value.st_mode)
        or stat.S_ISLNK(value.st_mode)
        or value.st_nlink != 1
        or stat.S_IMODE(value.st_mode) != 0o600
        or (owner is not None and (value.st_uid, value.st_gid) != owner)
    ):
        raise OSError


def _validate_directory_metadata(value: os.stat_result) -> None:
    owner = _effective_owner()
    if (
        not stat.S_ISDIR(value.st_mode)
        or stat.S_ISLNK(value.st_mode)
        or stat.S_IMODE(value.st_mode) != 0o700
        or (owner is not None and (value.st_uid, value.st_gid) != owner)
    ):
        raise OSError


def _stable(path: Path, maximum: int) -> bytes:
    descriptor = -1
    try:
        before = path.lstat()
        _validate_file_metadata(before)
        if before.st_size <= 0 or before.st_size > maximum:
            raise OSError
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        _validate_file_metadata(opened)
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = path.lstat()
        if _identity(before) != _identity(opened) or _identity(opened) != _identity(after):
            raise OSError
        if len(raw) != opened.st_size:
            raise OSError
        return raw
    except OSError as error:
        raise FinalReviewSubmissionWorkerError("final-review evidence unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def read_stable_openai_secret(path: Path = OPENAI_SECRET_PATH) -> str:
    descriptor = -1
    try:
        before = path.lstat()
        owner = _effective_owner()
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o400
            or (owner is not None and (before.st_uid, before.st_gid) != owner)
            or before.st_size <= 0
            or before.st_size > SECRET_MAX_BYTES
        ):
            raise OSError
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        chunks: list[bytes] = []
        remaining = SECRET_MAX_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = path.lstat()
        if _identity(before) != _identity(opened) or _identity(opened) != _identity(after) or len(raw) != opened.st_size:
            raise OSError
    except OSError as error:
        raise FinalReviewSubmissionWorkerError("final-review secret unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        secret = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise FinalReviewSubmissionWorkerError("final-review secret unavailable") from error
    if secret.endswith("\n"):
        secret = secret[:-1]
    if not secret or any(character.isspace() for character in secret):
        raise FinalReviewSubmissionWorkerError("final-review secret unavailable")
    return secret


def _parse_cost_cap(value: str) -> int:
    try:
        parsed = int(value, 10)
    except (TypeError, ValueError) as error:
        raise FinalReviewSubmissionWorkerError("final-review request invalid") from error
    if str(parsed) != value or parsed <= 0 or parsed > contract.MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise FinalReviewSubmissionWorkerError("final-review request invalid")
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
        raise FinalReviewSubmissionWorkerError("final-review request invalid") from error


def _reject_ambient_secrets(environment: Mapping[str, str]) -> None:
    forbidden = ("OPENAI_API_KEY", "API_KEY", "AWS_", "AZURE_", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
    if any(any(token in key.upper() for token in forbidden) for key in environment):
        raise FinalReviewSubmissionWorkerError("provider environment invalid")


def _run_directory(run_id: str, review_root: Path = PRIVATE_REVIEW_RUNS_ROOT) -> Path:
    try:
        root = review_root.resolve(strict=True)
        _validate_directory_metadata(root.lstat())
        candidate = review_root / run_id
        metadata = candidate.lstat()
        _validate_directory_metadata(metadata)
        if (
            candidate.resolve(strict=True) != candidate
            or candidate.resolve(strict=False).parent != root
        ):
            raise OSError
        return candidate.resolve(strict=True)
    except OSError as error:
        raise FinalReviewSubmissionWorkerError("final-review run unavailable") from error


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


def _classes(contents: Mapping[str, bytes]) -> tuple[dict[str, bytes], ...]:
    artifacts: dict[str, bytes] = {}
    journals: dict[str, bytes] = {}
    outputs: dict[str, bytes] = {}
    derived: dict[str, bytes] = {}
    for name, raw in contents.items():
        if name == STATE:
            continue
        if name.startswith(".") and "-submission-" in name:
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


def _expected_names(state: SpeakerReviewRunState) -> tuple[set[str], set[str]]:
    if state.status not in {
        SpeakerReviewRunStatus.FINAL_REVIEW_PREPARED,
        SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED,
    }:
        raise FinalReviewSubmissionWorkerError("final-review checkpoint invalid")
    final_count = state.final_review_part_count
    if type(final_count) is not int or final_count <= 0:
        raise FinalReviewSubmissionWorkerError("final-review checkpoint invalid")
    required = {
        STATE,
        "candidates.jsonl",
        "source-manifest.json",
        "primary-verdicts.jsonl",
        "primary-parse-errors.json",
        "primary-decisions.jsonl",
        "adjudication-verdicts.jsonl",
        "adjudication-parse-errors.json",
        DEFAULT_SPEAKER_REVIEW_CONFIGURATION.final_decisions_filename,
        *(
            f"primary-part-{part:04d}-requests.jsonl"
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
            f"adjudication-part-{part:04d}-output.jsonl"
            for part in range(1, state.adjudication_part_count + 1)
        ),
        *(f"final-review-part-{part:04d}-requests.jsonl" for part in range(1, final_count + 1)),
        *(
            f".{stage}-part-{part:04d}-submission-{kind}.json"
            for stage, count in (
                ("primary", state.primary_part_count),
                ("adjudication", state.adjudication_part_count),
            )
            for part in range(1, count + 1)
            for kind in ("intent", "completed")
        ),
    }
    optional = {
        *(
            f"primary-part-{part:04d}-api-errors.jsonl"
            for part in range(1, state.primary_part_count + 1)
        ),
        *(
            f"adjudication-part-{part:04d}-api-errors.jsonl"
            for part in range(1, state.adjudication_part_count + 1)
        ),
    }
    if state.status is SpeakerReviewRunStatus.FINAL_REVIEW_PREPARED:
        # A crash can leave one or both final-review journal records.  Admit
        # them to the inventory so the worker can return reconciliation_required
        # without reading the secret or retrying the provider.
        optional.update({
            ".final-review-part-0001-submission-intent.json",
            ".final-review-part-0001-submission-completed.json",
        })
    if state.status is SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED:
        required.update(
            ".final-review-part-0001-submission-intent.json",
            ".final-review-part-0001-submission-completed.json",
        )
    return required, optional


def _inventory(run: Path) -> tuple[dict[str, bytes], SpeakerReviewRunState]:
    try:
        _validate_directory_metadata(run.lstat())
        names = {entry.name for entry in run.iterdir()}
        if STATE not in names:
            raise OSError
        canonical, state = load_validated_run_state(run, DEFAULT_SPEAKER_REVIEW_CONFIGURATION)
        if canonical != run or state.run_id != run.name:
            raise ValueError
        required, optional = _expected_names(state)
        if not required <= names or not names <= required | optional:
            raise OSError
        contents: dict[str, bytes] = {}
        total = 0
        for name in sorted(names):
            raw = _stable(run / name, STATE_MAX_BYTES if name == STATE else ARTIFACT_MAX_BYTES)
            total += len(raw)
            if total > TOTAL_MAX_BYTES:
                raise OSError
            contents[name] = raw
        state_raw = (
            json.dumps(state.to_dict(), ensure_ascii=False, sort_keys=True, indent=2).encode()
            + b"\n"
        )
        if contents[STATE] != state_raw:
            raise ValueError
        return contents, state
    except FinalReviewSubmissionWorkerError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise FinalReviewSubmissionWorkerError("final-review inventory invalid") from error


def _digest_bindings(
    contents: Mapping[str, bytes],
    environment: Mapping[str, str],
    state: SpeakerReviewRunState,
) -> None:
    names = (
        contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256,
        contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256,
        contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256,
        contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256,
        contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256,
    )
    expected = {name: environment.get(name, "") for name in names}
    if any(
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
        for value in expected.values()
    ):
        raise FinalReviewSubmissionWorkerError("final-review checkpoint binding invalid")
    artifacts, journals, outputs, derived = _classes(contents)
    if state.status is SpeakerReviewRunStatus.FINAL_REVIEW_PREPARED:
        # The root intent binds the journal set before the provider call. A
        # crash may durably add the exact part-one intent/completed pair while
        # leaving the state at PREPARED. Authenticate the original set here;
        # the two admitted recovery records are validated separately below.
        journals = {
            name: raw
            for name, raw in journals.items()
            if name
            not in {
                ".final-review-part-0001-submission-intent.json",
                ".final-review-part-0001-submission-completed.json",
            }
        }
    actual = {
        contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: _sha(contents[STATE]),
        contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: _set_digest(artifacts),
        contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: _set_digest(journals),
        contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256: _set_digest(outputs),
        contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256: _set_digest(derived),
    }
    if actual != expected:
        raise FinalReviewSubmissionWorkerError("final-review checkpoint changed")


def _expected_request_hash(contents: Mapping[str, bytes], environment: Mapping[str, str]) -> str:
    expected = environment.get(contract.ENV_EXPECTED_REQUEST_SHA256, "")
    if (
        not isinstance(expected, str)
        or len(expected) != 64
        or any(c not in "0123456789abcdef" for c in expected)
    ):
        raise FinalReviewSubmissionWorkerError("final-review request binding invalid")
    request = contents.get("final-review-part-0001-requests.jsonl")
    if request is None or _sha(request) != expected:
        raise FinalReviewSubmissionWorkerError("final-review request changed")
    return expected


def _parse_requests(
    contents: Mapping[str, bytes], state: SpeakerReviewRunState
) -> tuple[dict[str, object], ...]:
    requests: list[dict[str, object]] = []
    try:
        for part in range(1, state.final_review_part_count + 1):
            raw = contents[f"final-review-part-{part:04d}-requests.jsonl"]
            for line in raw.splitlines():
                value = json.loads(line.decode("utf-8"), object_pairs_hook=contract._pairs)
                if not isinstance(value, dict):
                    raise ValueError
                requests.append(value)
    except (KeyError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise FinalReviewSubmissionWorkerError("final-review request invalid") from error
    if not requests:
        raise FinalReviewSubmissionWorkerError("final-review request unavailable")
    return tuple(requests)


def _cost_micros(value: float) -> int:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise FinalReviewSubmissionWorkerError("final-review cost invalid")
    try:
        micros = Decimal(str(value)) * Decimal(1_000_000)
        if not micros.is_finite() or micros < 0:
            raise ValueError
        result = int(micros.to_integral_value(rounding=ROUND_CEILING))
    except (InvalidOperation, ValueError, ArithmeticError) as error:
        raise FinalReviewSubmissionWorkerError("final-review cost invalid") from error
    if result > contract.MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise FinalReviewSubmissionWorkerError("final-review cost invalid")
    return result


def _aggregate(
    state: SpeakerReviewRunState, *, status: str, estimated: int, submitted: int
) -> dict[str, object]:
    try:
        return contract.validate_aggregate(
            {
                "actual_primary_cost_microusd": _cost_micros(state.actual_primary_cost_usd),
                "actual_adjudication_cost_microusd": _cost_micros(
                    state.actual_adjudication_cost_usd
                ),
                "estimated_final_review_cost_microusd": estimated,
                "final_review_completed_part_count": state.final_review_completed_part_count,
                "final_review_part_count": state.final_review_part_count,
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
        raise FinalReviewSubmissionWorkerError("final-review aggregate invalid") from error


def _binding(request: bytes, state: SpeakerReviewRunState) -> dict[str, object]:
    return {
        "schema_version": 1,
        "request_sha256": _sha(request),
        "run_id": state.run_id,
        "stage": "final-review",
        "part": 1,
        "prompt_version": state.prompt_version,
        "batch_endpoint": DEFAULT_SPEAKER_REVIEW_CONFIGURATION.batch_endpoint,
        "completion_window": DEFAULT_SPEAKER_REVIEW_CONFIGURATION.batch_completion_window,
    }


def _validate_journals(
    run: Path, contents: Mapping[str, bytes], state: SpeakerReviewRunState
) -> None:
    ids = state.final_review_batch_ids
    inputs = state.final_review_input_file_ids
    if (
        len(ids) != 1
        or len(inputs) != 1
        or state.final_review_batch_id != ids[0]
        or state.final_review_input_file_id != inputs[0]
    ):
        raise FinalReviewSubmissionWorkerError("final-review evidence invalid")
    request = contents["final-review-part-0001-requests.jsonl"]
    binding = _binding(request, state)
    try:
        intent = _read_submission_record(
            _submission_path(run, "final-review", 0, "intent"), completed=False
        )
        completed = _read_submission_record(
            _submission_path(run, "final-review", 0, "completed"), completed=True
        )
    except (KeyError, RuntimeError) as error:
        raise FinalReviewSubmissionWorkerError("final-review evidence invalid") from error
    if (
        intent is None
        or completed is None
        or intent.get("binding") != binding
        or completed.get("binding") != binding
        or completed.get("batch_id") != ids[0]
        or completed.get("input_file_id") != inputs[0]
    ):
        raise FinalReviewSubmissionWorkerError("final-review evidence invalid")


def _recover_prepared_submission(
    run: Path,
    contents: Mapping[str, bytes],
    state: SpeakerReviewRunState,
    *,
    maximum_authorized_cost_usd: float,
) -> SpeakerReviewRunState:
    """Promote exact application journals after a worker crash, without egress."""

    request = contents["final-review-part-0001-requests.jsonl"]
    binding = _binding(request, state)
    try:
        intent = _read_submission_record(
            _submission_path(run, "final-review", 0, "intent"), completed=False
        )
        completed = _read_submission_record(
            _submission_path(run, "final-review", 0, "completed"), completed=True
        )
    except (KeyError, RuntimeError) as error:
        raise FinalReviewSubmissionWorkerError("final-review evidence invalid") from error
    if (
        intent is None
        or completed is None
        or intent.get("binding") != binding
        or completed.get("binding") != binding
        or not isinstance(completed.get("batch_id"), str)
        or not completed["batch_id"].strip()
        or not isinstance(completed.get("input_file_id"), str)
        or not completed["input_file_id"].strip()
        or not isinstance(completed.get("status"), str)
        or not completed["status"].strip()
    ):
        raise FinalReviewSubmissionWorkerError("final-review evidence invalid")
    try:
        returned, updated = _replay_workflow(
            expected_request_sha256=_sha(request),
            maximum_authorized_cost_usd=maximum_authorized_cost_usd,
        ).final_review(run, verified_run_state=state)
        if returned != run or updated.status is not SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED:
            raise ValueError
        return updated
    except (KeyError, TypeError, ValueError, OSError, RuntimeError, AssertionError) as error:
        raise FinalReviewSubmissionWorkerError("final-review recovery failed") from error


def _validate_prepared(state: SpeakerReviewRunState, contents: Mapping[str, bytes]) -> None:
    if (
        state.status is not SpeakerReviewRunStatus.FINAL_REVIEW_PREPARED
        or state.final_review_completed_part_count != 0
        or state.final_review_batch_ids
        or state.final_review_input_file_ids
        or state.final_review_batch_id is not None
        or state.final_review_input_file_id is not None
        or state.actual_final_review_cost_usd != 0.0
        or any(
            name.startswith("final-review-part-0001-")
            and name.endswith(("-output.jsonl", "-api-errors.jsonl"))
            for name in contents
        )
    ):
        raise FinalReviewSubmissionWorkerError("final-review checkpoint invalid")


def _workflow(
    secret: str, *, expected_request_sha256: str, maximum_authorized_cost_usd: float
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
        expected_final_review_request_sha256=expected_request_sha256,
        maximum_authorized_cost_usd=maximum_authorized_cost_usd,
    )
    return SpeakerReviewGraphWorkflow(review)


class _ProviderDisabledGateway:
    def submit(self, *args: object, **kwargs: object) -> BatchSubmission:
        raise AssertionError("provider access disabled during replay")

    def retrieve(self, *args: object, **kwargs: object) -> BatchSnapshot:
        raise AssertionError("provider access disabled during replay")

    def download_file(self, *args: object, **kwargs: object) -> str:
        raise AssertionError("provider access disabled during replay")


def _replay_workflow(
    *, expected_request_sha256: str | None = None, maximum_authorized_cost_usd: float | None = None
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
        expected_final_review_request_sha256=expected_request_sha256,
        maximum_authorized_cost_usd=maximum_authorized_cost_usd,
    )
    return SpeakerReviewGraphWorkflow(review)


def submit_final_review(
    *,
    environment: Mapping[str, str] | None = None,
    secret_path: Path = OPENAI_SECRET_PATH,
    review_root: Path = PRIVATE_REVIEW_RUNS_ROOT,
) -> dict[str, object]:
    values = os.environ if environment is None else environment
    _reject_ambient_secrets(values)
    request = request_from_environment(environment)
    run = _run_directory(str(request["run_id"]), review_root)
    contents, state = _inventory(run)
    _digest_bindings(contents, values, state)
    expected_request_hash = _expected_request_hash(contents, values)
    requests = _parse_requests(contents, state)
    try:
        estimate = estimate_batch_cost_usd(
            requests=requests,
            model=DEFAULT_MODEL_CONFIGURATION.speaker_final_review_model,
            configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        )
    except Exception as error:
        raise FinalReviewSubmissionWorkerError("final-review cost invalid") from error
    estimate_micros = _cost_micros(estimate)
    actual = _cost_micros(state.actual_primary_cost_usd) + _cost_micros(
        state.actual_adjudication_cost_usd
    )
    cap_value = request["maximum_authorized_cost_microusd"]
    if type(cap_value) is not int:
        raise FinalReviewSubmissionWorkerError("final-review request invalid")
    cap = cap_value
    configured = _cost_micros(DEFAULT_SPEAKER_REVIEW_CONFIGURATION.maximum_run_cost_usd)
    if actual + estimate_micros > cap or actual + estimate_micros > configured:
        raise FinalReviewSubmissionWorkerError("final-review cost exceeds authorization")

    if state.status is SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED:
        _validate_journals(run, contents, state)
        if any(
            name.startswith("final-review-part-0001-")
            and name.endswith(("-output.jsonl", "-api-errors.jsonl"))
            for name in contents
        ):
            raise FinalReviewSubmissionWorkerError("final-review replay output invalid")
        try:
            replay_directory, replay_state = _replay_workflow(
                expected_request_sha256=expected_request_hash,
                maximum_authorized_cost_usd=cap / 1_000_000,
            ).final_review(
                run, verified_run_state=state
            )
        except (RuntimeError, AssertionError) as error:
            raise FinalReviewSubmissionWorkerError("final-review replay invalid") from error
        if replay_directory != run or replay_state != state:
            raise FinalReviewSubmissionWorkerError("final-review replay invalid")
        return _aggregate(state, status="already_submitted", estimated=estimate_micros, submitted=1)

    _validate_prepared(state, contents)
    intent_name = ".final-review-part-0001-submission-intent.json"
    completed_name = ".final-review-part-0001-submission-completed.json"
    intent_exists, completed_exists = intent_name in contents, completed_name in contents
    if intent_exists or completed_exists:
        if not (intent_exists and completed_exists):
            return _aggregate(
                state, status="reconciliation_required", estimated=estimate_micros, submitted=0
            )
        recovered = _recover_prepared_submission(
            run,
            contents,
            state,
            maximum_authorized_cost_usd=cap / 1_000_000,
        )
        return _aggregate(recovered, status="submitted", estimated=estimate_micros, submitted=1)

    secret = read_stable_openai_secret(secret_path)
    try:
        returned, updated = _workflow(
            secret,
            expected_request_sha256=expected_request_hash,
            maximum_authorized_cost_usd=cap / 1_000_000,
        ).final_review(run, verified_run_state=state)
    except RuntimeError as error:
        if contract.is_reconciliation_error(error):
            return _aggregate(
                state, status="reconciliation_required", estimated=estimate_micros, submitted=0
            )
        raise FinalReviewSubmissionWorkerError("final-review submission failed") from error
    if returned != run or updated.status is not SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED:
        raise FinalReviewSubmissionWorkerError("final-review transition invalid")
    if (
        updated.final_review_completed_part_count != 0
        or len(updated.final_review_batch_ids) != 1
        or len(updated.final_review_input_file_ids) != 1
    ):
        raise FinalReviewSubmissionWorkerError("final-review transition invalid")
    return _aggregate(updated, status="submitted", estimated=estimate_micros, submitted=1)


submit_final_review_part = submit_final_review


def main() -> int:
    try:
        sys.stdout.buffer.write(contract.canonical_json(submit_final_review()))
        return 0
    except Exception:
        sys.stderr.write("error=speaker_review_final_review_submission_failed\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
