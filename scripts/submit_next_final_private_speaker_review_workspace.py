"""Isolated egress worker for exactly final-review part two.

The worker receives only a digest-bound run directory and the OpenAI secret.
It cannot observe, parse, finalize, promote, ingest, or submit another part.
"""

# ruff: noqa: E402, I001

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

from cinegraph.adapters.llm.openai_speaker_review_batch_gateway import (
    OpenAISpeakerReviewBatchGateway,  # noqa: E402
)
from cinegraph.adapters.workflow.langgraph.speaker_review_graph import (
    SpeakerReviewGraphWorkflow,  # noqa: E402
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
    private_speaker_review_next_final_review_submission_contract as contract,  # noqa: E402
)

PRIVATE_REVIEW_RUNS_ROOT = Path("/review-workspace/review-runs")
OPENAI_SECRET_PATH = Path("/run/secrets/openai_api_key")
SECRET_MAX_BYTES = 4_096
STATE_MAX_BYTES = 64 * 1024
ARTIFACT_MAX_BYTES = 64 * 1024 * 1024
TOTAL_MAX_BYTES = 256 * 1024 * 1024
STATE = "run-state.json"


class NextFinalReviewSubmissionWorkerError(RuntimeError):
    """Generic failure that never contains private evidence."""


def _owner() -> tuple[int, int] | None:
    if hasattr(os, "geteuid") and hasattr(os, "getegid"):
        return int(os.geteuid()), int(os.getegid())
    return None


def _file(value: os.stat_result) -> None:
    owner = _owner()
    if not stat.S_ISREG(value.st_mode) or stat.S_ISLNK(value.st_mode) or value.st_nlink != 1 or stat.S_IMODE(value.st_mode) != 0o600 or (owner is not None and (value.st_uid, value.st_gid) != owner):
        raise OSError


def _directory(value: os.stat_result) -> None:
    owner = _owner()
    if not stat.S_ISDIR(value.st_mode) or stat.S_ISLNK(value.st_mode) or stat.S_IMODE(value.st_mode) != 0o700 or (owner is not None and (value.st_uid, value.st_gid) != owner):
        raise OSError


def _stable(path: Path, maximum: int) -> bytes:
    descriptor = -1
    try:
        before = path.lstat()
        _file(before)
        if before.st_size <= 0 or before.st_size > maximum:
            raise OSError
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        _file(opened)
        raw = b""
        while len(raw) <= maximum:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - len(raw)))
            if not chunk:
                break
            raw += chunk
        after = path.lstat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or len(raw) != opened.st_size:
            raise OSError
        return raw
    except OSError as error:
        raise NextFinalReviewSubmissionWorkerError("final-review evidence unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def read_stable_openai_secret(path: Path = OPENAI_SECRET_PATH) -> str:
    try:
        value = path.lstat()
        owner = _owner()
        if not stat.S_ISREG(value.st_mode) or stat.S_ISLNK(value.st_mode) or value.st_nlink != 1 or stat.S_IMODE(value.st_mode) != 0o400 or (owner is not None and (value.st_uid, value.st_gid) != owner) or value.st_size <= 0 or value.st_size > SECRET_MAX_BYTES:
            raise OSError
        raw = _stable_secret(path)
    except OSError as error:
        raise NextFinalReviewSubmissionWorkerError("final-review secret unavailable") from error
    try:
        secret = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise NextFinalReviewSubmissionWorkerError("final-review secret unavailable") from error
    if secret.endswith("\n"):
        secret = secret[:-1]
    if not secret or any(character.isspace() for character in secret):
        raise NextFinalReviewSubmissionWorkerError("final-review secret unavailable")
    return secret


def _stable_secret(path: Path) -> bytes:
    before = path.lstat()
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        raw = os.read(descriptor, SECRET_MAX_BYTES + 1)
        value = os.fstat(descriptor)
        after = path.lstat()
        if (
            len(raw) != value.st_size
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
            or (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            raise OSError
        return raw
    finally:
        os.close(descriptor)


def _run_directory(run_id: str, root: Path) -> Path:
    try:
        root = root.resolve(strict=True)
        _directory(root.lstat())
        run = root / run_id
        _directory(run.lstat())
        if run.resolve(strict=True).parent != root:
            raise OSError
        return run
    except OSError as error:
        raise NextFinalReviewSubmissionWorkerError("final-review run unavailable") from error


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _set_digest(contents: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(contents):
        encoded = name.encode()
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(contents[name]).to_bytes(8, "big"))
        digest.update(contents[name])
    return digest.hexdigest()


def _classes(contents: Mapping[str, bytes]) -> tuple[dict[str, bytes], ...]:
    groups = ({}, {}, {}, {})
    for name, raw in contents.items():
        if name == STATE:
            continue
        if name.startswith(".") and "-submission-" in name:
            groups[1][name] = raw
        elif name.endswith("-output.jsonl") or name.endswith("-api-errors.jsonl"):
            groups[2][name] = raw
        elif name.endswith("-requests.jsonl") or name in {
            "candidates.jsonl",
            "source-manifest.json",
        }:
            groups[0][name] = raw
        else:
            groups[3][name] = raw
    return groups


def _expected_names(state: SpeakerReviewRunState) -> tuple[set[str], set[str]]:
    count = state.final_review_part_count
    if state.status is not SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED or type(count) is not int or count <= 0 or state.final_review_completed_part_count != contract.PREDECESSOR_COMPLETED_PART_COUNT:
        raise NextFinalReviewSubmissionWorkerError("final-review checkpoint invalid")
    required = {STATE, "candidates.jsonl", "source-manifest.json", "primary-verdicts.jsonl", "primary-parse-errors.json", "primary-decisions.jsonl", "adjudication-verdicts.jsonl", "adjudication-parse-errors.json", DEFAULT_SPEAKER_REVIEW_CONFIGURATION.final_decisions_filename}
    required.update(f"primary-part-{part:04d}-{suffix}.jsonl" for part in range(1, state.primary_part_count + 1) for suffix in ("requests", "output"))
    required.update(f"adjudication-part-{part:04d}-{suffix}.jsonl" for part in range(1, state.adjudication_part_count + 1) for suffix in ("requests", "output"))
    required.update(f"final-review-part-{part:04d}-requests.jsonl" for part in range(1, count + 1))
    required.add("final-review-part-0001-output.jsonl")
    required.update(f".{stage}-part-{part:04d}-submission-{kind}.json" for stage, total in (("primary", state.primary_part_count), ("adjudication", state.adjudication_part_count), ("final-review", 1)) for part in range(1, total + 1) for kind in ("intent", "completed"))
    optional = {f"primary-part-{part:04d}-api-errors.jsonl" for part in range(1, state.primary_part_count + 1)} | {f"adjudication-part-{part:04d}-api-errors.jsonl" for part in range(1, state.adjudication_part_count + 1)} | {"final-review-part-0001-api-errors.jsonl", ".final-review-part-0002-submission-intent.json", ".final-review-part-0002-submission-completed.json", "final-review-part-0002-output.jsonl", "final-review-part-0002-api-errors.jsonl"}
    return required, optional


def _inventory(run: Path) -> tuple[dict[str, bytes], SpeakerReviewRunState]:
    try:
        names = {item.name for item in run.iterdir()}
        state_raw = _stable(run / STATE, STATE_MAX_BYTES)
        canonical, state = load_validated_run_state(run, DEFAULT_SPEAKER_REVIEW_CONFIGURATION)
        required, optional = _expected_names(state)
        if canonical != run or state.run_id != run.name or not required <= names or not names <= required | optional:
            raise OSError
        contents: dict[str, bytes] = {}
        total = 0
        for name in sorted(names):
            raw = state_raw if name == STATE else _stable(run / name, ARTIFACT_MAX_BYTES)
            total += len(raw)
            if total > TOTAL_MAX_BYTES:
                raise OSError
            contents[name] = raw
        if contents[STATE] != json.dumps(state.to_dict(), ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n":
            raise ValueError
        return contents, state
    except NextFinalReviewSubmissionWorkerError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise NextFinalReviewSubmissionWorkerError("final-review inventory invalid") from error


def _bind(contents: Mapping[str, bytes], environment: Mapping[str, str]) -> bytes:
    names = (contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256, contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256, contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256, contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256, contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256)
    expected = {name: environment.get(name, "") for name in names}
    if any(len(value) != 64 or any(c not in "0123456789abcdef" for c in value) for value in expected.values()):
        raise NextFinalReviewSubmissionWorkerError("final-review checkpoint binding invalid")
    artifacts, journals, outputs, derived = _classes(contents)
    actual = {contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: _sha(contents[STATE]), contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: _set_digest(artifacts), contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: _set_digest(journals), contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256: _set_digest(outputs), contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256: _set_digest(derived)}
    if actual != expected:
        raise NextFinalReviewSubmissionWorkerError("final-review checkpoint changed")
    request = contents.get("final-review-part-0002-requests.jsonl")
    expected_request = environment.get(contract.ENV_EXPECTED_REQUEST_SHA256, "")
    if request is None or len(expected_request) != 64 or _sha(request) != expected_request:
        raise NextFinalReviewSubmissionWorkerError("final-review request changed")
    return request


def _cost(value: float) -> int:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise NextFinalReviewSubmissionWorkerError("final-review cost invalid")
    try:
        result = int((Decimal(str(value)) * Decimal(1_000_000)).to_integral_value(rounding=ROUND_CEILING))
    except (InvalidOperation, ValueError, ArithmeticError) as error:
        raise NextFinalReviewSubmissionWorkerError("final-review cost invalid") from error
    if result < 0 or result > contract.MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise NextFinalReviewSubmissionWorkerError("final-review cost invalid")
    return result


def _aggregate(state: SpeakerReviewRunState, *, status: str, estimated: int, submitted: int) -> dict[str, object]:
    return contract.validate_aggregate({"actual_adjudication_cost_microusd": _cost(state.actual_adjudication_cost_usd), "actual_final_review_cost_microusd": _cost(state.actual_final_review_cost_usd), "actual_primary_cost_microusd": _cost(state.actual_primary_cost_usd), "estimated_final_review_cost_microusd": estimated, "final_review_completed_part_count": state.final_review_completed_part_count, "final_review_part_count": state.final_review_part_count, "operation": contract.OPERATION, "purpose": contract.PURPOSE, "run_id": state.run_id, "run_status": state.status.value, "season_number": contract.SEASON_NUMBER, "status": status, "submitted_part_count": submitted}, status=status)


def _workflow(secret: str, expected_request_sha256: str, maximum_authorized_cost_usd: float) -> SpeakerReviewGraphWorkflow:
    from openai import OpenAI
    models = DEFAULT_MODEL_CONFIGURATION
    review = SpeakerReviewWorkflow(gateway=OpenAISpeakerReviewBatchGateway(OpenAI(api_key=secret), DEFAULT_SPEAKER_REVIEW_CONFIGURATION), configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION, primary_model=models.speaker_review_model, adjudication_model=models.speaker_adjudication_model, final_review_model=models.speaker_final_review_model, primary_reasoning_effort=models.speaker_review_reasoning_effort, adjudication_reasoning_effort=models.speaker_adjudication_reasoning_effort, final_review_reasoning_effort=models.speaker_final_review_reasoning_effort, expected_next_final_review_request_sha256=expected_request_sha256, maximum_authorized_cost_usd=maximum_authorized_cost_usd)
    return SpeakerReviewGraphWorkflow(review)


class _ProviderDisabledGateway:
    def submit(self, *args: object, **kwargs: object) -> BatchSubmission:
        raise AssertionError("provider access disabled during replay")
    def retrieve(self, *args: object, **kwargs: object) -> BatchSnapshot:
        raise AssertionError("provider access disabled during replay")
    def download_file(self, *args: object, **kwargs: object) -> str:
        raise AssertionError("provider access disabled during replay")


def _replay(expected_request_sha256: str, maximum_authorized_cost_usd: float) -> SpeakerReviewGraphWorkflow:
    models = DEFAULT_MODEL_CONFIGURATION
    review = SpeakerReviewWorkflow(gateway=_ProviderDisabledGateway(), configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION, primary_model=models.speaker_review_model, adjudication_model=models.speaker_adjudication_model, final_review_model=models.speaker_final_review_model, primary_reasoning_effort=models.speaker_review_reasoning_effort, adjudication_reasoning_effort=models.speaker_adjudication_reasoning_effort, final_review_reasoning_effort=models.speaker_final_review_reasoning_effort, expected_next_final_review_request_sha256=expected_request_sha256, maximum_authorized_cost_usd=maximum_authorized_cost_usd)
    return SpeakerReviewGraphWorkflow(review)


def _request(environment: Mapping[str, str]) -> dict[str, object]:
    try:
        cap = int(environment[contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD], 10)
        return contract.validate_request({"archive_sha256": environment[contract.ENV_ARCHIVE_SHA256], "authorization_id": environment[contract.ENV_AUTHORIZATION_ID], "maximum_authorized_cost_microusd": cap, "operation": contract.OPERATION, "purpose": contract.PURPOSE, "run_id": environment[contract.ENV_RUN_ID], "schema_version": contract.PROTOCOL_VERSION, "season_number": contract.SEASON_NUMBER})
    except (KeyError, TypeError, ValueError) as error:
        raise NextFinalReviewSubmissionWorkerError("final-review request invalid") from error


def _reject_ambient(environment: Mapping[str, str]) -> None:
    if any(
        token in key.upper()
        for key in environment
        for token in (
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_ORG",
            "OPENAI_PROJECT",
            "API_KEY",
            "AWS_",
            "AZURE_",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "REQUESTS_CA_BUNDLE",
            "CURL_CA_BUNDLE",
        )
    ):
        raise NextFinalReviewSubmissionWorkerError("provider environment invalid")


def submit_next_final_review(*, environment: Mapping[str, str] | None = None, secret_path: Path = OPENAI_SECRET_PATH, review_root: Path = PRIVATE_REVIEW_RUNS_ROOT) -> dict[str, object]:
    values = os.environ if environment is None else environment
    _reject_ambient(values)
    request = _request(values)
    run = _run_directory(str(request["run_id"]), review_root)
    contents, state = _inventory(run)
    if (
        state.final_review_part_count == contract.PREDECESSOR_COMPLETED_PART_COUNT
        and state.final_review_completed_part_count
        == contract.PREDECESSOR_COMPLETED_PART_COUNT
    ):
        # The root coordinator normally exits before Compose. Keep this guard
        # in the worker as a defence-in-depth provider-free terminal path.
        spent = (
            _cost(state.actual_primary_cost_usd)
            + _cost(state.actual_adjudication_cost_usd)
            + _cost(state.actual_final_review_cost_usd)
        )
        if spent > int(request["maximum_authorized_cost_microusd"]) or spent > _cost(
            state.maximum_cost_usd
        ):
            raise NextFinalReviewSubmissionWorkerError(
                "final-review cost exceeds authorization"
            )
        return _aggregate(state, status="all_parts_completed", estimated=0, submitted=0)
    request_raw = _bind(contents, values)
    requests = [json.loads(line.decode("utf-8")) for line in request_raw.splitlines() if line]
    if not requests:
        raise NextFinalReviewSubmissionWorkerError("final-review request unavailable")
    estimate = _cost(estimate_batch_cost_usd(requests=requests, model=DEFAULT_MODEL_CONFIGURATION.speaker_final_review_model, configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION))
    cap = int(request["maximum_authorized_cost_microusd"])
    prior = _cost(state.actual_primary_cost_usd) + _cost(state.actual_adjudication_cost_usd) + _cost(state.actual_final_review_cost_usd)
    if prior + estimate > cap or prior + estimate > _cost(state.maximum_cost_usd):
        raise NextFinalReviewSubmissionWorkerError("final-review cost exceeds authorization")
    intent2 = ".final-review-part-0002-submission-intent.json" in contents
    completed2 = ".final-review-part-0002-submission-completed.json" in contents
    if len(state.final_review_batch_ids) == contract.SUBMITTED_PART_NUMBER and len(state.final_review_input_file_ids) == contract.SUBMITTED_PART_NUMBER and not (intent2 and completed2):
        return _aggregate(
            state, status="reconciliation_required", estimated=estimate, submitted=0
        )
    if intent2 or completed2:
        if not (intent2 and completed2) or len(state.final_review_batch_ids) != contract.SUBMITTED_PART_NUMBER:
            return _aggregate(
                state, status="reconciliation_required", estimated=estimate, submitted=0
            )
        if any(
            name in contents
            for name in (
                "final-review-part-0002-output.jsonl",
                "final-review-part-0002-api-errors.jsonl",
            )
        ):
            raise NextFinalReviewSubmissionWorkerError("final-review part-two output is not allowed")
    if (
        len(state.final_review_batch_ids) == contract.SUBMITTED_PART_NUMBER
        and len(state.final_review_input_file_ids) == contract.SUBMITTED_PART_NUMBER
    ):
        replay = _replay(_sha(request_raw), cap / 1_000_000)
        method = getattr(replay, "submit_next_final_review", None) or getattr(
            replay, "submit_next_final_review_part", None
        )
        if method is None:
            raise NextFinalReviewSubmissionWorkerError("final-review replay unavailable")
        returned, replayed = method(run, verified_run_state=state)
        if returned != run or replayed != state:
            raise NextFinalReviewSubmissionWorkerError("final-review replay invalid")
        return _aggregate(state, status="already_submitted", estimated=estimate, submitted=1)
    secret = read_stable_openai_secret(secret_path)
    try:
        graph = _workflow(secret, _sha(request_raw), cap / 1_000_000)
        method = getattr(graph, "submit_next_final_review", None) or getattr(graph, "submit_next_final_review_part", None)
        if method is None:
            raise RuntimeError("graph method unavailable")
        returned, updated = method(run, verified_run_state=state)
    except RuntimeError as error:
        if contract.is_reconciliation_error(error):
            return _aggregate(state, status="reconciliation_required", estimated=estimate, submitted=0)
        raise NextFinalReviewSubmissionWorkerError("final-review submission failed") from error
    if returned != run or updated.final_review_completed_part_count != contract.PREDECESSOR_COMPLETED_PART_COUNT or len(updated.final_review_batch_ids) != contract.SUBMITTED_PART_NUMBER:
        raise NextFinalReviewSubmissionWorkerError("final-review transition invalid")
    return _aggregate(updated, status="submitted", estimated=estimate, submitted=1)


submit_next_final_review_part = submit_next_final_review


def main() -> int:
    try:
        sys.stdout.buffer.write(contract.canonical_json(submit_next_final_review()))
        return 0
    except Exception:
        sys.stderr.write("error=speaker_review_next_final_review_submission_failed\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
