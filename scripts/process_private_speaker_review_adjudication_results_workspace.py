"""Secretless isolated worker for provider-free adjudication-result processing."""

from __future__ import annotations

import json
import os
import sys
from decimal import ROUND_CEILING, Decimal
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
from cinegraph.ingestion.speaker_review.workflow import (  # noqa: E402
    SpeakerReviewWorkflow,
    load_validated_run_state,
)
from scripts import (  # noqa: E402
    private_speaker_review_adjudication_result_processing_contract as contract,
)
from scripts import (  # noqa: E402
    process_private_speaker_review_results_workspace as _primary_worker,
)

RUNS_ROOT = Path("/review-workspace/review-runs")
MAX_FILE_BYTES = 64 * 1024 * 1024


class AdjudicationResultProcessingWorkerError(RuntimeError):
    """Failure that deliberately contains no private evidence."""


class OfflineGateway:
    def submit(self, *args: object, **kwargs: object) -> object:
        raise AdjudicationResultProcessingWorkerError("provider access disabled")

    def retrieve(self, *args: object, **kwargs: object) -> object:
        raise AdjudicationResultProcessingWorkerError("provider access disabled")

    def download_file(self, *args: object, **kwargs: object) -> object:
        raise AdjudicationResultProcessingWorkerError("provider access disabled")


def _stable(path: Path) -> bytes:
    """Use the Phase 67 fd/lstat-stable reader for the isolated mount."""
    try:
        return _primary_worker._read_stable(path, MAX_FILE_BYTES)
    except Exception as error:
        raise AdjudicationResultProcessingWorkerError("processing evidence unavailable") from error


def _digest(files: Mapping[str, bytes]) -> str:
    return _primary_worker._set_digest(files)


def _inventory(run_directory: Path) -> dict[str, object]:
    try:
        root_before = run_directory.lstat()
        _primary_worker._validate_owner_mode(root_before, directory=True)
        groups: dict[str, object] = {
            "artifacts": {},
            "requests": {},
            "journals": {},
            "outputs": {},
            "derived": {},
            "directories": {},
        }
        directory_identities: dict[str, tuple[int, int, int, int, int]] = {
            ".": _primary_worker._identity(root_before)
        }
        total = 0
        for current, directories, filenames in os.walk(run_directory, followlinks=False):
            current_path = Path(current)
            current_relative = current_path.relative_to(run_directory).as_posix()
            if current_relative != ".":
                current_metadata = current_path.lstat()
                _primary_worker._validate_owner_mode(current_metadata, directory=True)
                directory_identities[current_relative] = _primary_worker._identity(
                    current_metadata
                )
            for directory in directories:
                child = current_path / directory
                child_metadata = child.lstat()
                _primary_worker._validate_owner_mode(child_metadata, directory=True)
                child_relative = child.relative_to(run_directory).as_posix()
                directory_identities[child_relative] = _primary_worker._identity(
                    child_metadata
                )
            for name in filenames:
                path = current_path / name
                relative = path.relative_to(run_directory).as_posix()
                raw = _stable(path)
                total += len(raw)
                if total > 256 * 1024 * 1024:
                    raise OSError
                if relative == "run-state.json":
                    groups["state"] = raw
                elif name.startswith(".") and "-submission-" in name:
                    groups["journals"][relative] = raw  # type: ignore[index]
                elif name.endswith("-requests.jsonl"):
                    groups["requests"][relative] = raw  # type: ignore[index]
                elif name.endswith("-output.jsonl") or name.endswith("-api-errors.jsonl"):
                    groups["outputs"][relative] = raw  # type: ignore[index]
                elif name in {"candidates.jsonl", "source-manifest.json"}:
                    groups["artifacts"][relative] = raw  # type: ignore[index]
                else:
                    groups["derived"][relative] = raw  # type: ignore[index]
        if "state" not in groups:
            raise OSError
        root_after = run_directory.lstat()
        _primary_worker._validate_owner_mode(root_after, directory=True)
        if _primary_worker._identity(root_after) != directory_identities["."]:
            raise OSError
        for name, identity in directory_identities.items():
            path = run_directory if name == "." else run_directory / name
            metadata = path.lstat()
            _primary_worker._validate_owner_mode(metadata, directory=True)
            if _primary_worker._identity(metadata) != identity:
                raise OSError
        groups["directory_identity"] = directory_identities.pop(".")
        groups["directories"] = directory_identities
        return groups
    except (OSError, RuntimeError) as error:
        raise AdjudicationResultProcessingWorkerError("processing inventory unavailable") from error


def _validate_inventory_shape(groups: Mapping[str, object], state: object) -> None:
    try:
        canonical_state = (
            json.dumps(
                state.to_dict(),  # type: ignore[attr-defined]
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (AttributeError, TypeError, ValueError) as error:
        raise AdjudicationResultProcessingWorkerError(
            "processing state invalid"
        ) from error
    if groups.get("state") != canonical_state:
        raise AdjudicationResultProcessingWorkerError("processing state invalid")
    artifacts = groups["artifacts"]  # type: ignore[assignment]
    requests = groups["requests"]  # type: ignore[assignment]
    journals = groups["journals"]  # type: ignore[assignment]
    outputs = groups["outputs"]  # type: ignore[assignment]
    derived = groups["derived"]  # type: ignore[assignment]
    directories = groups["directories"]  # type: ignore[assignment]
    if set(artifacts) != {"candidates.jsonl", "source-manifest.json"}:
        raise AdjudicationResultProcessingWorkerError("processing inventory changed")
    part_counts = {
        "primary": state.primary_part_count,  # type: ignore[attr-defined]
        "adjudication": state.adjudication_part_count,  # type: ignore[attr-defined]
    }
    if any(type(count) is not int or count <= 0 for count in part_counts.values()):
        raise AdjudicationResultProcessingWorkerError("processing inventory invalid")
    expected_requests = {
        f"{stage}-part-{index:04d}-requests.jsonl"
        for stage, count in part_counts.items()
        for index in range(1, count + 1)
    }
    expected_journal_all = {
        f".{stage}-part-{index:04d}-submission-{kind}.json"
        for stage, count in part_counts.items()
        for index in range(1, count + 1)
        for kind in ("intent", "completed")
    }
    if set(journals) != expected_journal_all:
        raise AdjudicationResultProcessingWorkerError("processing inventory changed")
    required_outputs = {
        f"{stage}-part-{index:04d}-output.jsonl"
        for stage, count in part_counts.items()
        for index in range(1, count + 1)
    }
    optional_outputs = {
        f"{stage}-part-{index:04d}-api-errors.jsonl"
        for stage, count in part_counts.items()
        for index in range(1, count + 1)
    }
    if not required_outputs <= set(outputs) <= required_outputs | optional_outputs:
        raise AdjudicationResultProcessingWorkerError("processing inventory changed")
    final_count = state.final_review_part_count  # type: ignore[attr-defined]
    if state.status.value == "final_review_prepared":  # type: ignore[attr-defined]
        if type(final_count) is not int or final_count <= 0:
            raise AdjudicationResultProcessingWorkerError("processing inventory invalid")
    else:
        final_count = 0
    expected_final = {f"final-review-part-{index:04d}-requests.jsonl" for index in range(1, final_count + 1)}
    if state.status.value == "adjudication_part_completed":  # type: ignore[attr-defined]
        allowed_partial_final = {
            f"final-review-part-{index:04d}-requests.jsonl"
            for index in range(1, state.candidate_count + 1)  # type: ignore[attr-defined]
        }
        if not expected_requests <= set(requests) <= expected_requests | allowed_partial_final:
            raise AdjudicationResultProcessingWorkerError("processing inventory changed")
    else:
        expected_requests.update(expected_final)
        if set(requests) != expected_requests:
            raise AdjudicationResultProcessingWorkerError("processing inventory changed")
    required_derived = {
        "primary-verdicts.jsonl",
        "primary-parse-errors.json",
        "primary-decisions.jsonl",
    }
    adjudication_derived = {
        "adjudication-verdicts.jsonl",
        "adjudication-parse-errors.json",
        DEFAULT_SPEAKER_REVIEW_CONFIGURATION.final_decisions_filename,
    }
    try:
        reviewed_files, completed_directories = _primary_worker._completed_output_inventory(
            artifacts["source-manifest.json"]
        )
    except Exception as error:
        raise AdjudicationResultProcessingWorkerError("processing inventory changed") from error
    completion_derived = reviewed_files | {
        "review-ledger.json",
        "calibration-sample.json",
    }
    allowed_derived = required_derived | adjudication_derived
    required_directories: set[str] = set()
    allowed_directories: set[str] = set()
    if state.status.value == "completed":  # type: ignore[attr-defined]
        required_derived |= adjudication_derived | completion_derived
        allowed_derived = set(required_derived)
        required_directories = completed_directories
        allowed_directories = completed_directories
    elif state.status.value == "final_review_prepared":  # type: ignore[attr-defined]
        required_derived |= adjudication_derived
        allowed_directories = set()
    elif state.status.value == "adjudication_part_completed":  # type: ignore[attr-defined]
        allowed_derived |= completion_derived
        allowed_directories = completed_directories
    elif state.status.value != "adjudication_part_completed":  # type: ignore[attr-defined]
        raise AdjudicationResultProcessingWorkerError("processing inventory invalid")
    if not required_derived <= set(derived) <= allowed_derived:
        raise AdjudicationResultProcessingWorkerError("processing inventory changed")
    if not required_directories <= set(directories) <= allowed_directories:
        raise AdjudicationResultProcessingWorkerError("processing inventory changed")


def _validate_inventory(run_directory: Path, state: object, values: Mapping[str, str]) -> dict[str, object]:
    groups = _inventory(run_directory)
    _validate_inventory_shape(groups, state)
    artifacts = groups["artifacts"]  # type: ignore[assignment]
    requests = groups["requests"]  # type: ignore[assignment]
    journals = groups["journals"]  # type: ignore[assignment]
    outputs = groups["outputs"]  # type: ignore[assignment]
    derived = groups["derived"]  # type: ignore[assignment]
    expected = {
        contract.ENV_EXPECTED_STATE_DIGEST: _primary_worker._expected_digest(values.get(contract.ENV_EXPECTED_STATE_DIGEST), "state digest"),
        contract.ENV_EXPECTED_ARTIFACTS_DIGEST: _primary_worker._expected_digest(values.get(contract.ENV_EXPECTED_ARTIFACTS_DIGEST), "artifact digest"),
        contract.ENV_EXPECTED_REQUESTS_DIGEST: _primary_worker._expected_digest(values.get(contract.ENV_EXPECTED_REQUESTS_DIGEST), "request digest"),
        contract.ENV_EXPECTED_JOURNALS_DIGEST: _primary_worker._expected_digest(values.get(contract.ENV_EXPECTED_JOURNALS_DIGEST), "journal digest"),
        contract.ENV_EXPECTED_OUTPUTS_DIGEST: _primary_worker._expected_digest(values.get(contract.ENV_EXPECTED_OUTPUTS_DIGEST), "output digest"),
        contract.ENV_EXPECTED_DERIVED_DIGEST: _primary_worker._expected_digest(values.get(contract.ENV_EXPECTED_DERIVED_DIGEST), "derived digest"),
    }
    if any(value is None for value in expected.values()):
        raise AdjudicationResultProcessingWorkerError("processing digest binding incomplete")
    checks = {
        contract.ENV_EXPECTED_STATE_DIGEST: _primary_worker._sha256(groups["state"]),
        contract.ENV_EXPECTED_ARTIFACTS_DIGEST: _digest(artifacts),
        contract.ENV_EXPECTED_REQUESTS_DIGEST: _digest(requests),
        contract.ENV_EXPECTED_JOURNALS_DIGEST: _digest(journals),
        contract.ENV_EXPECTED_OUTPUTS_DIGEST: _digest(outputs),
        contract.ENV_EXPECTED_DERIVED_DIGEST: _digest(derived),
    }
    if any(checks[key] != expected[key] for key in checks):
        raise AdjudicationResultProcessingWorkerError("processing digest binding changed")
    return groups


def _run_dir(run_id: str) -> Path:
    try:
        return _primary_worker._run_directory(run_id, RUNS_ROOT)
    except Exception as error:
        raise AdjudicationResultProcessingWorkerError(
            "processing run unavailable"
        ) from error


def _authorized_cost_microusd(value: object) -> int:
    if not isinstance(value, str):
        raise AdjudicationResultProcessingWorkerError("processing request invalid")
    try:
        parsed = int(value, 10)
    except ValueError as error:
        raise AdjudicationResultProcessingWorkerError(
            "processing request invalid"
        ) from error
    if str(parsed) != value:
        raise AdjudicationResultProcessingWorkerError("processing request invalid")
    return parsed


def _reject_ambient_secrets(environment: Mapping[str, str]) -> None:
    forbidden = ("OPENAI", "API_KEY", "AWS_", "AZURE_", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
    if any(any(token in key.upper() for token in forbidden) for key in environment):
        raise AdjudicationResultProcessingWorkerError("provider access disabled")


def _workflow(maximum_authorized_cost_usd: float) -> SpeakerReviewGraphWorkflow:
    models = DEFAULT_MODEL_CONFIGURATION
    workflow = SpeakerReviewWorkflow(
        gateway=OfflineGateway(),
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        primary_model=models.speaker_review_model,
        adjudication_model=models.speaker_adjudication_model,
        final_review_model=models.speaker_final_review_model,
        primary_reasoning_effort=models.speaker_review_reasoning_effort,
        adjudication_reasoning_effort=models.speaker_adjudication_reasoning_effort,
        final_review_reasoning_effort=models.speaker_final_review_reasoning_effort,
        maximum_authorized_cost_usd=maximum_authorized_cost_usd,
    )
    return SpeakerReviewGraphWorkflow(workflow)


def _aggregate(
    state: object,
    *,
    status: str,
    maximum_authorized_cost_microusd: int,
) -> dict[str, object]:
    def micros(value: object) -> int:
        try:
            decimal = Decimal(str(value)) * Decimal(1_000_000)
            if not decimal.is_finite() or decimal < 0:
                raise ValueError
            return int(decimal.to_integral_value(rounding=ROUND_CEILING))
        except (TypeError, ValueError, ArithmeticError) as error:
            raise AdjudicationResultProcessingWorkerError("processing cost invalid") from error

    value = {
        "accepted_by_consensus": state.accepted_by_consensus,  # type: ignore[attr-defined]
        "accepted_by_adjudication": state.accepted_by_adjudication,  # type: ignore[attr-defined]
        "actual_adjudication_cost_microusd": micros(state.actual_adjudication_cost_usd),  # type: ignore[attr-defined]
        "actual_primary_cost_microusd": micros(state.actual_primary_cost_usd),  # type: ignore[attr-defined]
        "adjudication_completed_part_count": state.adjudication_completed_part_count,  # type: ignore[attr-defined]
        "adjudication_part_count": state.adjudication_part_count,  # type: ignore[attr-defined]
        "candidate_count": state.candidate_count,  # type: ignore[attr-defined]
        "final_review_part_count": state.final_review_part_count,  # type: ignore[attr-defined]
        "maximum_authorized_cost_microusd": maximum_authorized_cost_microusd,
        "needs_human": state.needs_human,  # type: ignore[attr-defined]
        "operation": contract.OPERATION,
        "primary_completed_part_count": state.primary_completed_part_count,  # type: ignore[attr-defined]
        "primary_part_count": state.primary_part_count,  # type: ignore[attr-defined]
        "purpose": contract.PURPOSE,
        "run_status": state.status.value,  # type: ignore[attr-defined]
        "season_number": contract.SEASON_NUMBER,
        "status": status,
    }
    return contract.validate_aggregate(value, status=status)


def process(environment: Mapping[str, str] | None = None) -> bytes:
    values = os.environ if environment is None else environment
    _reject_ambient_secrets(values)
    maximum_authorized_cost_microusd = _authorized_cost_microusd(
        values.get(contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD)
    )
    request = contract.validate_request(
        {
            "archive_sha256": values.get(contract.ENV_ARCHIVE_SHA256, ""),
            "authorization_id": values.get(contract.ENV_AUTHORIZATION_ID, ""),
            "maximum_authorized_cost_microusd": maximum_authorized_cost_microusd,
            "operation": contract.OPERATION,
            "purpose": contract.PURPOSE,
            "run_id": values.get(contract.ENV_RUN_ID, ""),
            "schema_version": contract.PROTOCOL_VERSION,
            "season_number": contract.SEASON_NUMBER,
        }
    )
    run_directory = _run_dir(str(request["run_id"]))
    canonical, state = load_validated_run_state(
        run_directory, DEFAULT_SPEAKER_REVIEW_CONFIGURATION
    )
    if canonical != run_directory or state.run_id != run_directory.name:
        raise AdjudicationResultProcessingWorkerError("processing run unavailable")
    before = _validate_inventory(run_directory, state, values)
    replay = state.status.value in {"final_review_prepared", "completed"}
    graph = _workflow(int(request["maximum_authorized_cost_microusd"]) / 1_000_000)
    _, after = graph.process_adjudication_results(run_directory, verified_run_state=state)
    after_canonical, persisted_after = load_validated_run_state(
        run_directory, DEFAULT_SPEAKER_REVIEW_CONFIGURATION
    )
    if (
        after_canonical != run_directory
        or persisted_after != after
        or after.run_id != run_directory.name
    ):
        raise AdjudicationResultProcessingWorkerError("processing state changed")
    after_inventory = _inventory(run_directory)
    _validate_inventory_shape(after_inventory, after)
    for key in ("artifacts", "requests", "journals", "outputs"):
        if key != "requests" and before[key] != after_inventory[key]:
            raise AdjudicationResultProcessingWorkerError("processing evidence changed")
    before_requests = before["requests"]  # type: ignore[assignment]
    after_requests = after_inventory["requests"]  # type: ignore[assignment]
    if any(after_requests.get(name) != raw for name, raw in before_requests.items()):
        raise AdjudicationResultProcessingWorkerError("processing evidence changed")
    prior_derived = before["derived"]  # type: ignore[assignment]
    after_derived = after_inventory["derived"]  # type: ignore[assignment]
    if any(after_derived.get(name) != raw for name, raw in prior_derived.items()):
        raise AdjudicationResultProcessingWorkerError("processing evidence changed")
    before_directories = before["directories"]  # type: ignore[assignment]
    after_directories = after_inventory["directories"]  # type: ignore[assignment]
    for name, identity in before_directories.items():
        observed = after_directories.get(name)
        if observed is None or (
            observed[0],
            observed[1],
            observed[4],
        ) != (identity[0], identity[1], identity[4]):
            raise AdjudicationResultProcessingWorkerError("processing evidence changed")
    before_root = before["directory_identity"]  # type: ignore[assignment]
    after_root = after_inventory["directory_identity"]  # type: ignore[assignment]
    if (before_root[0], before_root[1], before_root[4]) != (
        after_root[0],
        after_root[1],
        after_root[4],
    ):
        raise AdjudicationResultProcessingWorkerError("processing evidence changed")
    if replay and before["state"] != after_inventory["state"]:
        raise AdjudicationResultProcessingWorkerError("processing state changed")
    return contract.canonical_json(
        _aggregate(
            after,
            status="already_processed" if replay else after.status.value,
            maximum_authorized_cost_microusd=maximum_authorized_cost_microusd,
        )
    )


def main() -> int:
    try:
        output = process()
        if len(output) > contract.OUTPUT_MAX_BYTES:
            raise AdjudicationResultProcessingWorkerError("processing aggregate invalid")
        sys.stdout.buffer.write(output)
        return 0
    except Exception as error:
        del error
        sys.stderr.write("adjudication-result processing failed\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
