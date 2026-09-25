"""Isolated, single-retrieve worker for final-review part one."""

# Imports intentionally follow the isolated release path setup below.
# ruff: noqa: E402

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping

_ROOT = Path(__file__).resolve().parents[1]
for _path in (_ROOT, _ROOT / "src"):
    if os.fspath(_path) not in sys.path:
        sys.path.insert(0, os.fspath(_path))

from cinegraph.adapters.workflow.langgraph.speaker_review_graph import (  # noqa: E402
    SpeakerReviewGraphWorkflow,
)
from cinegraph.config import (  # noqa: E402
    DEFAULT_MODEL_CONFIGURATION,
    DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
)
from cinegraph.domain.enums.enum import SpeakerReviewRunStatus  # noqa: E402
from cinegraph.ingestion.speaker_review.workflow import (  # noqa: E402
    SpeakerReviewWorkflow,
    load_validated_run_state,
)
from scripts import (
    private_speaker_review_final_review_observation_contract as contract,  # noqa: E402
)
from scripts import submit_final_private_speaker_review_workspace as submission  # noqa: E402
from scripts.observe_fourth_private_speaker_review_adjudication_workspace import (  # noqa: E402
    read_stable_openai_secret,
)

RUNS_ROOT = Path("/review-workspace/review-runs")
OPENAI_SECRET_PATH = Path("/run/secrets/openai_api_key")


class FinalReviewObservationWorkerError(RuntimeError):
    """Generic failure that never discloses provider or private review data."""


def _expected_digest(value: str | None) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise FinalReviewObservationWorkerError("observation binding invalid")
    return value


def _request(environment: Mapping[str, str]) -> dict[str, object]:
    try:
        cap_raw = environment[contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD]
        cap = int(cap_raw, 10)
        if str(cap) != cap_raw:
            raise ValueError
        return contract.validate_request({
            "archive_sha256": environment[contract.ENV_ARCHIVE_SHA256],
            "authorization_id": environment[contract.ENV_AUTHORIZATION_ID],
            "maximum_authorized_cost_microusd": cap,
            "operation": contract.OPERATION,
            "purpose": contract.PURPOSE,
            "run_id": environment[contract.ENV_RUN_ID],
            "schema_version": contract.PROTOCOL_VERSION,
            "season_number": contract.SEASON_NUMBER,
        })
    except (KeyError, TypeError, ValueError) as error:
        raise FinalReviewObservationWorkerError("observation request invalid") from error


def _bound_inventory(
    contents: Mapping[str, bytes],
    environment: Mapping[str, str],
    state: object,
) -> tuple[object, int]:
    try:
        submission._digest_bindings(contents, environment, state)
        submission._expected_request_hash(contents, environment)
        request_raw = contents["final-review-part-0001-requests.jsonl"]
    except Exception as error:
        raise FinalReviewObservationWorkerError("observation checkpoint changed") from error
    estimate_raw = environment.get(contract.ENV_EXPECTED_ESTIMATED_FINAL_REVIEW_COST_MICROUSD)
    try:
        estimate = int(estimate_raw or "", 10)
        if str(estimate) != estimate_raw or estimate < 0 or estimate > contract.MAXIMUM_AUTHORIZED_COST_MICROUSD:
            raise ValueError
    except ValueError as error:
        raise FinalReviewObservationWorkerError("observation cost binding invalid") from error
    return request_raw, estimate


def _aggregate(state: object, request: Mapping[str, object], estimate: int, status: str) -> dict[str, object]:
    def micros(value: float) -> int:
        try:
            return submission._cost_micros(value)
        except submission.FinalReviewSubmissionWorkerError as error:
            raise ValueError("invalid review cost") from error

    try:
        value = {
            "actual_adjudication_cost_microusd": micros(state.actual_adjudication_cost_usd),
            "actual_final_review_cost_microusd": 0,
            "actual_primary_cost_microusd": micros(state.actual_primary_cost_usd),
            "estimated_final_review_cost_microusd": estimate,
            "final_review_completed_part_count": state.final_review_completed_part_count,
            "final_review_part_count": state.final_review_part_count,
            "maximum_authorized_cost_microusd": request["maximum_authorized_cost_microusd"],
            "state_maximum_cost_microusd": micros(state.maximum_cost_usd),
            "operation": contract.OPERATION,
            "purpose": contract.PURPOSE,
            "run_id": state.run_id,
            "run_status": state.status.value,
            "season_number": contract.SEASON_NUMBER,
            "status": status,
        }
        return contract.validate_aggregate(value, status=status)
    except (AttributeError, TypeError, ValueError) as error:
        raise FinalReviewObservationWorkerError("observation aggregate invalid") from error


def _workflow(gateway: object) -> SpeakerReviewGraphWorkflow:
    models = DEFAULT_MODEL_CONFIGURATION
    return SpeakerReviewGraphWorkflow(SpeakerReviewWorkflow(
        gateway=gateway,  # type: ignore[arg-type]
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        primary_model=models.speaker_review_model,
        adjudication_model=models.speaker_adjudication_model,
        final_review_model=models.speaker_final_review_model,
        primary_reasoning_effort=models.speaker_review_reasoning_effort,
        adjudication_reasoning_effort=models.speaker_adjudication_reasoning_effort,
        final_review_reasoning_effort=models.speaker_final_review_reasoning_effort,
    ))


def observe_final_review_part_one(
    *, environment: Mapping[str, str] | None = None,
    review_root: Path = RUNS_ROOT,
    secret_path: Path = OPENAI_SECRET_PATH,
) -> dict[str, object]:
    values = os.environ if environment is None else environment
    try:
        submission._reject_ambient_secrets(values)
    except submission.FinalReviewSubmissionWorkerError as error:
        raise FinalReviewObservationWorkerError("observation provider environment invalid") from error
    request = _request(values)
    try:
        run = submission._run_directory(str(request["run_id"]), review_root)
        canonical, state = load_validated_run_state(run, DEFAULT_SPEAKER_REVIEW_CONFIGURATION)
        if canonical != run or state.run_id != request["run_id"]:
            raise ValueError
        contents, inventory_state = submission._inventory(run)
        if inventory_state != state:
            raise ValueError
        _, estimate = _bound_inventory(contents, values, state)
    except FinalReviewObservationWorkerError:
        raise
    except Exception as error:
        raise FinalReviewObservationWorkerError("observation checkpoint invalid") from error
    expected_receipt = _expected_digest(values.get(contract.ENV_EXPECTED_SUBMISSION_RECEIPT_SHA256))
    del expected_receipt  # The root coordinator authenticates the receipt and owns exact replay.
    if state.status is not SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED or state.final_review_completed_part_count != 0:
        raise FinalReviewObservationWorkerError("observation checkpoint invalid")
    try:
        actual = submission._cost_micros(state.actual_primary_cost_usd) + submission._cost_micros(
            state.actual_adjudication_cost_usd
        )
    except submission.FinalReviewSubmissionWorkerError as error:
        raise FinalReviewObservationWorkerError("observation cost invalid") from error
    cap = int(request["maximum_authorized_cost_microusd"])
    try:
        state_cap = submission._cost_micros(state.maximum_cost_usd)
    except submission.FinalReviewSubmissionWorkerError as error:
        raise FinalReviewObservationWorkerError("observation cost invalid") from error
    if actual + estimate > min(cap, state_cap):
        raise FinalReviewObservationWorkerError("observation cost exceeds authorization")
    try:
        secret = read_stable_openai_secret(secret_path)
        from openai import OpenAI

        client = OpenAI(api_key=secret)
        from cinegraph.adapters.llm.openai_speaker_review_batch_gateway import (  # noqa: PLC0415
            OpenAISpeakerReviewBatchGateway,
        )

        workflow = _workflow(OpenAISpeakerReviewBatchGateway(client, DEFAULT_SPEAKER_REVIEW_CONFIGURATION))
        _, observed = workflow.observe_final_review_part_one(run, verified_run_state=state)
    except RuntimeError as error:
        if str(error).startswith("OpenAI Batch ") and " ended with status " in str(error):
            _, failed = load_validated_run_state(run, DEFAULT_SPEAKER_REVIEW_CONFIGURATION)
            return _aggregate(failed, request, estimate, "failed")
        raise FinalReviewObservationWorkerError("final-review observation failed") from error
    except Exception as error:
        raise FinalReviewObservationWorkerError("final-review observation failed") from error
    return _aggregate(
        observed,
        request,
        estimate,
        "observed" if observed.final_review_completed_part_count == 1 else "waiting",
    )


def main() -> int:
    try:
        result = observe_final_review_part_one()
        sys.stdout.buffer.write(contract.canonical_json(result))
        return 0
    except FinalReviewObservationWorkerError:
        sys.stderr.write("error=speaker_review_final_review_observation_failed\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
