from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from scripts import (
    private_speaker_review_primary_result_processing_contract as contract,
)
from scripts import process_private_speaker_review_results_workspace as worker

from cinegraph.config import DEFAULT_SPEAKER_REVIEW_CONFIGURATION
from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review.workflow import SpeakerReviewRunState

RUN_ID = "speaker-review-0123456789abcdef"
AUTHORIZATION_ID = "123e4567-e89b-42d3-a456-426614174000"
DIGEST = "a" * 64


def _environment(run: Path) -> dict[str, str]:
    snapshot = worker._read_inventory(run)
    return {
        contract.ENV_ARCHIVE_SHA256: DIGEST,
        contract.ENV_AUTHORIZATION_ID: AUTHORIZATION_ID,
        contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: "5000000",
        contract.ENV_RUN_ID: RUN_ID,
        contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: worker._sha256(snapshot.state),
        contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: worker._set_digest(
            snapshot.artifacts
        ),
        contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: worker._set_digest(
            snapshot.journals
        ),
        contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256: worker._set_digest(
            snapshot.outputs
        ),
        contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256: worker._set_digest(
            snapshot.derived
        ),
    }


@pytest.fixture(autouse=True)
def _worker_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name == "posix":
        monkeypatch.setattr(worker, "WORKER_UID", os.getuid())
        monkeypatch.setattr(worker, "WORKER_GID", os.getgid())


def _state(
    status: SpeakerReviewRunStatus = SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED,
) -> SpeakerReviewRunState:
    return SpeakerReviewRunState(
        schema_version=DEFAULT_SPEAKER_REVIEW_CONFIGURATION.schema_version,
        run_id=RUN_ID,
        status=status,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        candidate_count=1,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        final_review_model="gpt-5.6-sol",
        prompt_version=DEFAULT_SPEAKER_REVIEW_CONFIGURATION.prompt_version,
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=0.25,
        actual_primary_cost_usd=0.0,
        actual_adjudication_cost_usd=0.0,
        primary_part_count=1,
        primary_completed_part_count=1,
        primary_batch_ids=("batch-1",),
        primary_input_file_ids=("input-1",),
        primary_batch_id="batch-1",
        primary_input_file_id="input-1",
    )


def _write_inventory(run: Path, state: SpeakerReviewRunState) -> None:
    contents = {
        "candidates.jsonl": b"{}\n",
        "source-manifest.json": b"{}\n",
        "run-state.json": (
            json.dumps(state.to_dict(), sort_keys=True, separators=(",", ":")) + "\n"
        ).encode(),
        "primary-part-0001-requests.jsonl": b'{"custom_id":"one"}\n',
        ".primary-part-0001-submission-intent.json": b"{}\n",
        ".primary-part-0001-submission-completed.json": b"{}\n",
        "primary-part-0001-output.jsonl": b'{"custom_id":"one"}\n',
    }
    for name, raw in contents.items():
        path = run / name
        path.write_bytes(raw)
        path.chmod(0o600)


def test_contract_is_canonical_and_has_only_small_aggregate() -> None:
    request = {
        "archive_sha256": DIGEST,
        "authorization_id": AUTHORIZATION_ID,
        "maximum_authorized_cost_microusd": 5_000_000,
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "schema_version": contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
    }
    assert contract.parse_request(contract.canonical_json(request)) == request
    with pytest.raises(ValueError):
        contract.validate_request({**request, "provider_batch_id": "private"})
    aggregate = {
        "accepted_by_consensus": 2,
        "adjudication_part_count": 0,
        "candidate_count": 2,
        "operation": contract.OPERATION,
        "primary_completed_part_count": 1,
        "primary_part_count": 1,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "run_status": "completed",
        "season_number": 2,
        "status": "completed",
        "needs_human": 0,
    }
    assert contract.parse_aggregate(contract.canonical_json(aggregate)) == aggregate


def test_worker_calls_provider_free_graph_with_verified_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = tmp_path / RUN_ID
    run.mkdir(mode=0o700)
    run.chmod(0o700)
    before = _state()
    _write_inventory(run, before)
    monkeypatch.setattr(worker, "_run_directory", lambda *_: run)
    monkeypatch.setattr(worker, "load_validated_run_state", lambda *_: (run, before))
    calls: list[tuple[Path, SpeakerReviewRunState]] = []

    class Graph:
        def process_primary_results(
            self, path: Path, *, verified_run_state: SpeakerReviewRunState
        ) -> tuple[Path, SpeakerReviewRunState]:
            calls.append((path, verified_run_state))
            return path, replace(
                verified_run_state,
                status=SpeakerReviewRunStatus.COMPLETED,
                accepted_by_consensus=1,
            )

    monkeypatch.setattr(worker, "_workflow", lambda *_: Graph())
    monkeypatch.setattr(worker, "_validate_post_inventory", lambda *_: None)
    result = worker.process_primary_results(environment=_environment(run))

    assert result["status"] == "completed"
    assert calls == [(run, before)]


def test_worker_processes_real_graph_without_provider_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = tmp_path / RUN_ID
    run.mkdir(mode=0o700)
    state = _state()
    _write_inventory(run, state)
    candidate = {
        "candidate_id": "candidate-1",
        "source_filename": "Modern.Family.S02E01.script-aligned.srt",
        "source_sha256": "b" * 64,
        "episode": {"season": 2, "episode": 1},
        "cue_number": 1,
        "line_number": 3,
        "proposed_speaker": "CLAIRE",
        "dialogue_text": "Hello.",
        "allowed_speakers": ["CLAIRE", "PHIL"],
        "evidence": [
            {
                "evidence_id": "evidence-1",
                "source": "screenplay",
                "speaker": "CLAIRE",
                "text": "Hello.",
                "similarity_score": 100.0,
            }
        ],
    }
    (run / "candidates.jsonl").write_text(
        json.dumps(candidate) + "\n",
        encoding="utf-8",
    )
    output_lines = []
    for pass_id in DEFAULT_SPEAKER_REVIEW_CONFIGURATION.primary_pass_ids:
        verdict = {
            "candidate_id": "candidate-1",
            "action": "accept_candidate",
            "speaker": "CLAIRE",
            "confidence": 0.5,
            "evidence_ids": ["evidence-1"],
            "rationale": "Bounded evidence.",
        }
        output_lines.append(
            json.dumps(
                {
                    "custom_id": f"candidate-1::{pass_id}",
                    "response": {
                        "status_code": 200,
                        "body": {
                            "id": f"response-{pass_id}",
                            "model": "gpt-5.6-luna",
                            "output_text": json.dumps(verdict),
                            "usage": {"input_tokens": 100, "output_tokens": 20},
                        },
                    },
                }
            )
        )
    (run / "primary-part-0001-output.jsonl").write_text(
        "\n".join(output_lines) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(worker, "_run_directory", lambda *_: run)
    monkeypatch.setattr(worker, "load_validated_run_state", lambda *_: (run, state))

    result = worker.process_primary_results(environment=_environment(run))

    assert result["status"] == "adjudication_prepared"
    assert result["adjudication_part_count"] == 1
    assert (run / "adjudication-part-0001-requests.jsonl").exists()
    assert not (run / ".adjudication-part-0001-submission-intent.json").exists()


def test_worker_replays_processed_state_without_graph_or_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = tmp_path / RUN_ID
    run.mkdir(mode=0o700)
    state = _state(SpeakerReviewRunStatus.ADJUDICATION_PREPARED)
    state = replace(state, adjudication_part_count=1)
    _write_inventory(run, state)
    for name in worker.PRIMARY_DERIVED_NAMES:
        (run / name).write_bytes(b"{}\n")
        (run / name).chmod(0o600)
    (run / "adjudication-part-0001-requests.jsonl").write_bytes(b"{}\n")
    (run / "adjudication-part-0001-requests.jsonl").chmod(0o600)
    monkeypatch.setattr(worker, "_run_directory", lambda *_: run)
    monkeypatch.setattr(worker, "load_validated_run_state", lambda *_: (run, state))

    class Graph:
        def process_primary_results(
            self, path: Path, *, verified_run_state: SpeakerReviewRunState
        ) -> tuple[Path, SpeakerReviewRunState]:
            return path, verified_run_state

    monkeypatch.setattr(worker, "_workflow", lambda *_: Graph())

    result = worker.process_primary_results(environment=_environment(run))

    assert result["status"] == "already_processed"
    assert result["run_status"] == "adjudication_prepared"


def test_worker_rejects_unexpected_or_unstable_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = tmp_path / RUN_ID
    run.mkdir(mode=0o700)
    state = _state()
    _write_inventory(run, state)
    (run / "unexpected.json").write_bytes(b"x\n")
    (run / "unexpected.json").chmod(0o600)
    monkeypatch.setattr(worker, "_run_directory", lambda *_: run)
    monkeypatch.setattr(worker, "load_validated_run_state", lambda *_: (run, state))
    with pytest.raises(worker.PrimaryResultProcessingWorkerError, match="inventory"):
        worker.process_primary_results(environment=_environment(run))


def test_worker_requires_every_root_computed_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = tmp_path / RUN_ID
    run.mkdir(mode=0o700)
    state = _state()
    _write_inventory(run, state)
    environment = _environment(run)
    environment.pop(contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256)
    monkeypatch.setattr(worker, "_run_directory", lambda *_: run)
    monkeypatch.setattr(worker, "load_validated_run_state", lambda *_: (run, state))

    with pytest.raises(worker.PrimaryResultProcessingWorkerError, match="incomplete"):
        worker.process_primary_results(environment=environment)


def test_worker_binds_optional_api_errors_into_output_digest(tmp_path: Path) -> None:
    run = tmp_path / RUN_ID
    run.mkdir(mode=0o700)
    _write_inventory(run, _state())
    error_path = run / "primary-part-0001-api-errors.jsonl"
    error_path.write_bytes(b'{"error":"bounded"}\n')
    error_path.chmod(0o600)

    snapshot = worker._read_inventory(run)

    assert error_path.name in snapshot.outputs
    assert error_path.name not in snapshot.artifacts


def test_worker_rejects_hardlinked_private_input(tmp_path: Path) -> None:
    run = tmp_path / RUN_ID
    run.mkdir(mode=0o700)
    _write_inventory(run, _state())
    os.link(run / "candidates.jsonl", tmp_path / "hardlink.jsonl")

    with pytest.raises(worker.PrimaryResultProcessingWorkerError, match="inventory"):
        worker._read_inventory(run)


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode invariant")
def test_worker_rejects_group_readable_private_input(tmp_path: Path) -> None:
    run = tmp_path / RUN_ID
    run.mkdir(mode=0o700)
    _write_inventory(run, _state())
    (run / "candidates.jsonl").chmod(0o640)

    with pytest.raises(worker.PrimaryResultProcessingWorkerError, match="inventory"):
        worker._read_inventory(run)
