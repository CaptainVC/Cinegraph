from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest
from scripts import private_speaker_review_adjudication_result_processing_contract as contract
from scripts import process_private_speaker_review_adjudication_results_workspace as worker
from tests.unit.ingestion.speaker_review.test_process_adjudication_results import (
    _fixture,
    _workflow,
)

from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review.workflow import save_run_state

AUTHORIZATION_ID = "123e4567-e89b-42d3-a456-426614174000"


@pytest.fixture(autouse=True)
def _worker_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name == "posix":
        monkeypatch.setattr(worker._primary_worker, "WORKER_UID", os.getuid())
        monkeypatch.setattr(worker._primary_worker, "WORKER_GID", os.getgid())


def _environment(run: Path) -> dict[str, str]:
    groups = worker._inventory(run)
    return {
        contract.ENV_ARCHIVE_SHA256: "a" * 64,
        contract.ENV_AUTHORIZATION_ID: AUTHORIZATION_ID,
        contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: "5000000",
        contract.ENV_RUN_ID: run.name,
        contract.ENV_EXPECTED_STATE_DIGEST: worker._primary_worker._sha256(groups["state"]),  # type: ignore[index]
        contract.ENV_EXPECTED_ARTIFACTS_DIGEST: worker._digest(groups["artifacts"]),  # type: ignore[arg-type]
        contract.ENV_EXPECTED_REQUESTS_DIGEST: worker._digest(groups["requests"]),  # type: ignore[arg-type]
        contract.ENV_EXPECTED_JOURNALS_DIGEST: worker._digest(groups["journals"]),  # type: ignore[arg-type]
        contract.ENV_EXPECTED_OUTPUTS_DIGEST: worker._digest(groups["outputs"]),  # type: ignore[arg-type]
        contract.ENV_EXPECTED_DERIVED_DIGEST: worker._digest(groups["derived"]),  # type: ignore[arg-type]
    }


def _prepared_run(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    run, state = _fixture(tmp_path)
    prepared = _workflow().process_adjudication_results(run, state)
    assert prepared.status.value == "final_review_prepared"
    return run, _environment(run)


def test_worker_fresh_unresolved_transition_prepares_final_review_without_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, state = _fixture(tmp_path)
    environment = _environment(run)
    monkeypatch.setattr(worker, "_run_dir", lambda _: run)

    result = worker.process(environment)

    aggregate = contract.parse_aggregate(result, status="final_review_prepared")
    assert aggregate["run_status"] == "final_review_prepared"
    assert aggregate["final_review_part_count"] == 1
    assert aggregate["needs_human"] == 1
    assert (run / "final-review-part-0001-requests.jsonl").is_file()
    assert (run / "run-state.json").read_bytes() != b""
    assert state.status is SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED


def test_worker_fresh_resolved_transition_completes_without_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, state = _fixture(tmp_path, adjudication_action="accept_candidate", adjudication_confidence=0.99)
    environment = _environment(run)
    monkeypatch.setattr(worker, "_run_dir", lambda _: run)

    result = worker.process(environment)

    aggregate = contract.parse_aggregate(result, status="completed")
    assert aggregate["run_status"] == "completed"
    assert aggregate["final_review_part_count"] == 0
    assert aggregate["needs_human"] == 0
    assert (run / "review-ledger.json").is_file()
    assert state.status is SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED


def test_worker_accepts_matching_partial_final_request_after_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, state = _fixture(tmp_path)
    prepared = _workflow().process_adjudication_results(run, state)
    final_request = (run / "final-review-part-0001-requests.jsonl").read_bytes()
    save_run_state(run, state)
    environment = _environment(run)
    monkeypatch.setattr(worker, "_run_dir", lambda _: run)

    result = worker.process(environment)

    assert contract.parse_aggregate(result, status="final_review_prepared")["run_status"] == "final_review_prepared"
    assert (run / "final-review-part-0001-requests.jsonl").read_bytes() == final_request
    assert prepared.status is SpeakerReviewRunStatus.FINAL_REVIEW_PREPARED


def test_worker_rejects_mismatched_partial_final_request_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, state = _fixture(tmp_path)
    _workflow().process_adjudication_results(run, state)
    request_path = run / "final-review-part-0001-requests.jsonl"
    request_path.write_bytes(request_path.read_bytes().replace(b"candidate-2", b"candidate-X"))
    save_run_state(run, state)
    environment = _environment(run)
    monkeypatch.setattr(worker, "_run_dir", lambda _: run)
    with pytest.raises(RuntimeError, match="reconciliation"):
        worker.process(environment)


def test_worker_recovers_matching_partial_completion_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, state = _fixture(tmp_path, adjudication_action="accept_candidate", adjudication_confidence=0.99)
    _workflow().process_adjudication_results(run, state)
    (run / "review-ledger.json").unlink()
    (run / "calibration-sample.json").unlink()
    save_run_state(run, state)
    environment = _environment(run)
    monkeypatch.setattr(worker, "_run_dir", lambda _: run)

    result = worker.process(environment)

    assert contract.parse_aggregate(result, status="completed")["run_status"] == "completed"
    assert (run / "review-ledger.json").is_file()
    assert (run / "calibration-sample.json").is_file()


def test_worker_rejects_mismatched_partial_completion_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, state = _fixture(tmp_path, adjudication_action="accept_candidate", adjudication_confidence=0.99)
    _workflow().process_adjudication_results(run, state)
    reviewed = next((run / "reviewed").rglob("*.automated-reviewed.srt"))
    reviewed.write_bytes(reviewed.read_bytes() + b"tampered")
    save_run_state(run, state)
    environment = _environment(run)
    monkeypatch.setattr(worker, "_run_dir", lambda _: run)
    with pytest.raises(RuntimeError, match="reconciliation"):
        worker.process(environment)


def test_worker_rejects_noncanonical_run_state_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = _prepared_run(tmp_path)
    environment = _environment(run)
    state_path = run / "run-state.json"
    state_path.write_bytes(state_path.read_bytes().replace(b"\n", b"\r\n", 1))
    monkeypatch.setattr(worker, "_run_dir", lambda _: run)
    with pytest.raises(worker.AdjudicationResultProcessingWorkerError, match="state"):
        worker.process(environment)


def test_worker_rejects_altered_replay_state_even_when_digest_is_rebound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = _prepared_run(tmp_path)
    from cinegraph.ingestion.speaker_review.workflow import load_run_state

    state_path = run / "run-state.json"
    altered = load_run_state(state_path)
    save_run_state(run, replace(altered, needs_human=0))
    environment = _environment(run)
    monkeypatch.setattr(worker, "_run_dir", lambda _: run)
    with pytest.raises(RuntimeError, match="reconciliation"):
        worker.process(environment)


def test_worker_replays_with_verified_pre_state_and_small_aggregate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, environment = _prepared_run(tmp_path)
    calls: list[tuple[Path, object]] = []

    class Graph:
        def process_adjudication_results(self, path: Path, *, verified_run_state: object):
            calls.append((path, verified_run_state))
            return path, verified_run_state

    monkeypatch.setattr(worker, "_run_dir", lambda _: run)
    monkeypatch.setattr(worker, "_workflow", lambda _: Graph())
    result = worker.process(environment)

    assert result.endswith(b"\n")
    aggregate = contract.parse_aggregate(result, status="already_processed")
    assert aggregate["status"] == "already_processed"
    assert aggregate["run_status"] == "final_review_prepared"
    assert calls and calls[0][0] == run


def test_worker_replays_completed_run_with_exact_reviewed_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, state = _fixture(tmp_path, adjudication_action="accept_candidate", adjudication_confidence=0.99)
    completed = _workflow().process_adjudication_results(run, state)
    assert completed.status is SpeakerReviewRunStatus.COMPLETED
    environment = _environment(run)

    class Graph:
        def process_adjudication_results(self, path: Path, *, verified_run_state: object):
            return path, verified_run_state

    monkeypatch.setattr(worker, "_run_dir", lambda _: run)
    monkeypatch.setattr(worker, "_workflow", lambda _: Graph())
    aggregate = contract.parse_aggregate(worker.process(environment), status="already_processed")
    assert aggregate["run_status"] == "completed"
    assert aggregate["final_review_part_count"] == 0
    assert aggregate["needs_human"] == 0
    assert (run / "review-ledger.json").is_file()
    assert (run / "calibration-sample.json").is_file()
    assert list((run / "reviewed").rglob("*.automated-reviewed.srt"))


def test_worker_rejects_ambient_provider_secrets_before_run_lookup(tmp_path: Path) -> None:
    called = False

    def run(_: str) -> Path:
        nonlocal called
        called = True
        return tmp_path

    environment = {"OPENAI_API_KEY": "secret"}
    with pytest.raises(worker.AdjudicationResultProcessingWorkerError, match="provider access disabled"):
        worker.process(environment)
    assert not called


def test_worker_requires_all_exact_digest_bindings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run, environment = _prepared_run(tmp_path)
    environment.pop(contract.ENV_EXPECTED_DERIVED_DIGEST)
    monkeypatch.setattr(worker, "_run_dir", lambda _: run)
    with pytest.raises(worker.AdjudicationResultProcessingWorkerError, match="digest"):
        worker.process(environment)


def test_worker_rejects_extra_inventory_before_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run, environment = _prepared_run(tmp_path)
    extra = run / "extra.json"
    extra.write_bytes(b"extra\n")
    monkeypatch.setattr(worker, "_run_dir", lambda _: run)
    with pytest.raises(worker.AdjudicationResultProcessingWorkerError, match="inventory|digest"):
        worker.process(environment)


@pytest.mark.parametrize(
    "name,content",
    [
        ("primary-part-9999-requests.jsonl", b'{"custom_id":"unexpected"}\n'),
        ("primary-part-0001-api-errors.jsonl", b'{"custom_id":"unexpected"}\n'),
        ("unknown-directory/extra.json", b"extra\n"),
    ],
)
def test_worker_rejects_unknown_request_output_or_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, content: bytes
) -> None:
    run, environment = _prepared_run(tmp_path)
    path = run / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    monkeypatch.setattr(worker, "_run_dir", lambda _: run)
    with pytest.raises(worker.AdjudicationResultProcessingWorkerError, match="inventory|digest"):
        worker.process(environment)


def test_worker_rejects_immutable_evidence_change_after_graph_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, environment = _prepared_run(tmp_path)

    class Graph:
        def process_adjudication_results(self, path: Path, *, verified_run_state: object):
            (path / "adjudication-part-0001-output.jsonl").write_bytes(b"changed\n")
            return path, verified_run_state

    monkeypatch.setattr(worker, "_run_dir", lambda _: run)
    monkeypatch.setattr(worker, "_workflow", lambda _: Graph())
    with pytest.raises(worker.AdjudicationResultProcessingWorkerError, match="evidence"):
        worker.process(environment)


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode and symlink invariants")
def test_worker_rejects_symlink_and_group_readable_private_evidence(tmp_path: Path) -> None:
    run, _ = _prepared_run(tmp_path)
    target = run / "candidates.jsonl"
    link = run / "candidate-link.jsonl"
    link.symlink_to(target)
    with pytest.raises(worker.AdjudicationResultProcessingWorkerError, match="inventory"):
        worker._inventory(run)
    link.unlink()
    target.chmod(0o640)
    with pytest.raises(worker.AdjudicationResultProcessingWorkerError, match="inventory"):
        worker._inventory(run)


def test_offline_gateway_fails_closed_without_provider_methods() -> None:
    gateway = worker.OfflineGateway()
    for operation in (gateway.submit, gateway.retrieve, gateway.download_file):
        with pytest.raises(worker.AdjudicationResultProcessingWorkerError, match="provider access disabled"):
            operation("private")


def test_aggregate_rounds_fractional_usd_costs_up_to_micro_usd(tmp_path: Path) -> None:
    _, state = _fixture(tmp_path, adjudication_action="accept_candidate", adjudication_confidence=0.99)
    state = replace(
        state,
        status=SpeakerReviewRunStatus.COMPLETED,
        accepted_by_adjudication=1,
        actual_primary_cost_usd=0.0000001,
        actual_adjudication_cost_usd=0.0000001,
    )

    aggregate = worker._aggregate(
        state,
        status="completed",
        maximum_authorized_cost_microusd=5_000_000,
    )

    assert aggregate["actual_primary_cost_microusd"] == 1
    assert aggregate["actual_adjudication_cost_microusd"] == 1
    assert aggregate["maximum_authorized_cost_microusd"] == 5_000_000
