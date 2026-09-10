from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from scripts import observe_private_speaker_review_workspace as worker
from scripts import private_speaker_review_observation_contract as contract

from cinegraph.common.error_messages import SpeakerReviewErrorMessages
from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review.workflow import SpeakerReviewRunState

RUN_ID = "speaker-review-0123456789abcdef"
AUTHORIZATION_ID = "123e4567-e89b-42d3-a456-426614174000"
DIGEST = "a" * 64


def _environment(*, cost: str = "5000000") -> dict[str, str]:
    return {
        contract.ENV_ARCHIVE_SHA256: DIGEST,
        contract.ENV_AUTHORIZATION_ID: AUTHORIZATION_ID,
        contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: cost,
        contract.ENV_RUN_ID: RUN_ID,
    }


def _state(
    *,
    status: SpeakerReviewRunStatus = SpeakerReviewRunStatus.PRIMARY_SUBMITTED,
    completed: int = 0,
) -> SpeakerReviewRunState:
    return SpeakerReviewRunState(
        schema_version=1,
        run_id=RUN_ID,
        status=status,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        candidate_count=2,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-luna",
        prompt_version="test-v1",
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=0.25,
        actual_primary_cost_usd=0.0,
        actual_adjudication_cost_usd=0.0,
        primary_batch_id="private-batch",
        primary_input_file_id="private-input",
        primary_part_count=2,
        primary_completed_part_count=completed,
        primary_batch_ids=("private-batch",),
        primary_input_file_ids=("private-input",),
    )


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    state: SpeakerReviewRunState,
) -> Path:
    run = tmp_path / RUN_ID
    run.mkdir()
    monkeypatch.setattr(worker, "_run_directory", lambda _run_id, _root: run)
    monkeypatch.setattr(worker, "load_validated_run_state", lambda *_: (run, state))
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda *_: "sk-test")
    return run


def _configure_bound_part_two(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, SpeakerReviewRunState, dict[str, str]]:
    state = replace(
        _state(completed=1),
        primary_batch_id="private-batch-2",
        primary_input_file_id="private-input-2",
        primary_batch_ids=("private-batch-1", "private-batch-2"),
        primary_input_file_ids=("private-input-1", "private-input-2"),
    )
    run = _configure(monkeypatch, tmp_path, state)
    contents = {
        "candidates.jsonl": b"{}\n",
        "source-manifest.json": b"{}\n",
        "run-state.json": (
            json.dumps(state.to_dict(), sort_keys=True, separators=(",", ":")) + "\n"
        ).encode(),
        "primary-part-0001-requests.jsonl": b'{"custom_id":"one"}\n',
        "primary-part-0002-requests.jsonl": b'{"custom_id":"two"}\n',
        ".primary-part-0001-submission-intent.json": b"{}\n",
        ".primary-part-0001-submission-completed.json": b"{}\n",
        ".primary-part-0002-submission-intent.json": b"{}\n",
        ".primary-part-0002-submission-completed.json": b"{}\n",
        "primary-part-0001-output.jsonl": b'{"response":"one"}\n',
    }
    for name, raw in contents.items():
        path = run / name
        path.write_bytes(raw)
        path.chmod(0o600)
    artifacts = {name: raw for name, raw in contents.items() if not name.startswith(".")}
    journals = {name: raw for name, raw in contents.items() if name.startswith(".")}
    environment = {
        **_environment(),
        contract.ENV_EXPECTED_PRIMARY_PART_NUMBER: "2",
        contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: worker._sha256(contents["run-state.json"]),
        contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: worker._set_digest(artifacts),
        contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: worker._set_digest(journals),
        contract.ENV_EXPECTED_REQUEST_SHA256: worker._sha256(
            contents["primary-part-0002-requests.jsonl"]
        ),
    }
    return run, state, environment


def test_contract_is_canonical_and_rejects_wrong_operation_or_shape() -> None:
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
    raw = contract.canonical_json(request)
    assert contract.parse_request(raw) == request
    with pytest.raises(ValueError):
        contract.validate_request({**request, "operation": "advance"})
    with pytest.raises(ValueError):
        contract.validate_request({**request, "provider_batch_id": "private"})
    with pytest.raises(ValueError):
        contract.validate_request({**request, "season_number": True})
    with pytest.raises(ValueError):
        contract.validate_aggregate(
            {
                "estimated_primary_cost_microusd": 250_000,
                "operation": contract.OPERATION,
                "primary_completed_part_count": 0,
                "primary_part_count": 2,
                "purpose": contract.PURPOSE,
                "run_id": RUN_ID,
                "run_status": "primary_submitted",
                "season_number": 2.0,
                "status": "waiting",
            }
        )


def test_worker_rejects_cost_before_secret_or_provider_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = replace(_state(), estimated_primary_cost_usd=6.0)
    _configure(monkeypatch, tmp_path, state)
    monkeypatch.setattr(
        worker,
        "read_stable_openai_secret",
        lambda *_: pytest.fail("secret must not be read"),
    )
    monkeypatch.setattr(worker, "_workflow", lambda *_: pytest.fail("client must not exist"))
    with pytest.raises(worker.ObservationWorkerError, match="cost"):
        worker.observe_primary(environment=_environment())


def test_run_directory_is_exactly_confined_to_the_mounted_review_root(tmp_path: Path) -> None:
    root = tmp_path / "review-runs"
    root.mkdir()
    path = worker._run_directory(RUN_ID, root)
    assert path == root / RUN_ID
    assert path.parent == root.resolve()


def test_pending_observation_invokes_only_one_retrieve_and_no_submit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state()
    run = _configure(monkeypatch, tmp_path, state)
    calls: list[str] = []

    class Gateway:
        def retrieve(self, batch_id: str) -> None:
            calls.append(f"retrieve:{batch_id}")

        def submit(self, *_: object) -> None:
            pytest.fail("submit must be unreachable")

    class Graph:
        def observe_primary(self, path: Path) -> tuple[Path, SpeakerReviewRunState]:
            assert path == run
            Gateway().retrieve("private-batch")
            return path, state

    monkeypatch.setattr(worker, "_workflow", lambda _: Graph())
    result = worker.observe_primary(environment=_environment())
    assert result["status"] == "waiting"
    assert result["run_status"] == "primary_submitted"
    assert calls == ["retrieve:private-batch"]


def test_bound_part_two_uses_verified_state_without_reloading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, state, environment = _configure_bound_part_two(monkeypatch, tmp_path)
    captured: dict[str, object] = {}

    class Graph:
        def observe_primary(
            self,
            path: Path,
            *,
            verified_run_state: SpeakerReviewRunState,
        ) -> tuple[Path, SpeakerReviewRunState]:
            captured["state"] = verified_run_state
            return path, verified_run_state

    monkeypatch.setattr(worker, "_workflow", lambda _: Graph())

    result = worker.observe_primary(environment=environment)

    assert result["status"] == "waiting"
    assert captured == {"state": state}
    assert run.is_dir()


def test_bound_checkpoint_drift_rejects_before_secret_or_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _, environment = _configure_bound_part_two(monkeypatch, tmp_path)
    (run / "primary-part-0002-requests.jsonl").write_bytes(b'{"tampered":true}\n')
    monkeypatch.setattr(
        worker,
        "read_stable_openai_secret",
        lambda *_: pytest.fail("secret must not be read"),
    )
    monkeypatch.setattr(worker, "_workflow", lambda *_: pytest.fail("provider must not exist"))

    with pytest.raises(worker.ObservationWorkerError, match="checkpoint changed"):
        worker.observe_primary(environment=environment)


def test_completed_observation_downloads_once_and_returns_safe_aggregate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _state()
    after = replace(
        before,
        status=SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED,
        primary_completed_part_count=1,
        updated_at="2026-01-01T00:01:00+00:00",
    )
    run = _configure(monkeypatch, tmp_path, before)
    calls: list[str] = []

    class Gateway:
        def retrieve(self, batch_id: str) -> None:
            calls.append(f"retrieve:{batch_id}")

        def download_file(self, file_id: str) -> str:
            calls.append(f"download:{file_id}")
            return "private output"

        def submit(self, *_: object) -> None:
            pytest.fail("submit must be unreachable")

    class Graph:
        def observe_primary(self, path: Path) -> tuple[Path, SpeakerReviewRunState]:
            assert path == run
            gateway = Gateway()
            gateway.retrieve("private-batch")
            gateway.download_file("private-output")
            return path, after

    monkeypatch.setattr(worker, "_workflow", lambda _: Graph())
    result = worker.observe_primary(environment=_environment())
    assert result["status"] == "observed"
    assert result["run_status"] == "primary_part_completed"
    assert set(result) == contract.AGGREGATE_KEYS
    assert calls == ["retrieve:private-batch", "download:private-output"]


def test_observation_rejects_wrong_returned_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state()
    run = _configure(monkeypatch, tmp_path, state)

    class Graph:
        def observe_primary(self, _: Path) -> tuple[Path, SpeakerReviewRunState]:
            return run.parent, state

    monkeypatch.setattr(worker, "_workflow", lambda _: Graph())
    with pytest.raises(worker.ObservationWorkerError, match="result"):
        worker.observe_primary(environment=_environment())


def test_observation_rejects_illegal_state_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _state()
    _configure(monkeypatch, tmp_path, before)
    after = replace(
        before,
        status=SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED,
        primary_completed_part_count=1,
        primary_model="tampered-model",
        updated_at="2026-01-01T00:01:00+00:00",
    )

    class Graph:
        def observe_primary(self, path: Path) -> tuple[Path, SpeakerReviewRunState]:
            return path, after

    monkeypatch.setattr(worker, "_workflow", lambda _: Graph())
    with pytest.raises(worker.ObservationWorkerError, match="result"):
        worker.observe_primary(environment=_environment())


def test_terminal_failure_returns_only_the_bounded_failed_aggregate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _state()
    failed = replace(
        before,
        status=SpeakerReviewRunStatus.FAILED,
        updated_at="2026-01-01T00:01:00+00:00",
    )
    _configure(monkeypatch, tmp_path, before)

    class Graph:
        def observe_primary(self, path: Path) -> tuple[Path, SpeakerReviewRunState]:
            return path, failed

    monkeypatch.setattr(worker, "_workflow", lambda _: Graph())

    result = worker.observe_primary(environment=_environment())

    assert result["status"] == "failed"
    assert result["run_status"] == "failed"
    assert set(result) == contract.AGGREGATE_KEYS
    assert all("private-batch" not in str(value) for value in result.values())


def test_exact_observation_reconciliation_error_returns_bounded_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state()
    _configure(monkeypatch, tmp_path, state)

    class Graph:
        def observe_primary(self, _: Path) -> tuple[Path, SpeakerReviewRunState]:
            raise RuntimeError(
                SpeakerReviewErrorMessages.PRIMARY_OBSERVATION_RECONCILIATION_REQUIRED
            )

    monkeypatch.setattr(worker, "_workflow", lambda _: Graph())

    result = worker.observe_primary(environment=_environment())

    assert result["status"] == "reconciliation_required"
    assert result["run_status"] == "primary_submitted"
    assert set(result) == contract.AGGREGATE_KEYS


def test_completed_primary_part_is_already_observed_without_secret_or_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state(status=SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED, completed=1)
    _configure(monkeypatch, tmp_path, state)
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda *_: pytest.fail("secret"))
    monkeypatch.setattr(worker, "_workflow", lambda *_: pytest.fail("provider"))
    result = worker.observe_primary(environment=_environment())
    assert result["status"] == "already_observed"


@pytest.mark.parametrize(
    ("status", "run_status", "completed"),
    [
        ("waiting", "primary_part_completed", 1),
        ("observed", "primary_submitted", 0),
        ("already_observed", "primary_submitted", 0),
        ("failed", "primary_submitted", 0),
        ("reconciliation_required", "primary_part_completed", 1),
    ],
)
def test_aggregate_status_is_bound_to_run_transition(
    status: str,
    run_status: str,
    completed: int,
) -> None:
    aggregate = {
        "estimated_primary_cost_microusd": 250_000,
        "operation": contract.OPERATION,
        "primary_completed_part_count": completed,
        "primary_part_count": 2,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "run_status": run_status,
        "season_number": 2,
        "status": status,
    }
    with pytest.raises(ValueError, match="aggregate"):
        contract.validate_aggregate(aggregate)


@pytest.mark.parametrize(
    "status",
    [
        SpeakerReviewRunStatus.PREPARED,
        SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED,
        SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED,
        SpeakerReviewRunStatus.COMPLETED,
        SpeakerReviewRunStatus.NEEDS_HUMAN,
        SpeakerReviewRunStatus.FAILED,
    ],
)
def test_unrelated_run_states_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: SpeakerReviewRunStatus,
) -> None:
    state = _state(status=status, completed=1 if status is SpeakerReviewRunStatus.FAILED else 0)
    _configure(monkeypatch, tmp_path, state)
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda *_: pytest.fail("secret"))
    monkeypatch.setattr(worker, "_workflow", lambda *_: pytest.fail("provider"))
    with pytest.raises(worker.ObservationWorkerError, match="state"):
        worker.observe_primary(environment=_environment())


def test_worker_source_has_no_submit_advance_or_poll_loop() -> None:
    source = Path(worker.__file__).read_text(encoding="utf-8")
    assert ".submit(" not in source
    assert ".advance(" not in source
    assert "while " not in source


def test_worker_main_suppresses_provider_filesystem_and_secret_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        worker,
        "observe_primary",
        lambda: (_ for _ in ()).throw(
            RuntimeError("sk-private /private/path provider-batch-private")
        ),
    )

    assert worker.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error=speaker_review_observation_failed\n"
