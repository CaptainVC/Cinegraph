from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest
import scripts.observe_final_private_speaker_review_workspace as worker
from scripts import private_speaker_review_final_review_observation_contract as contract
from scripts import submit_final_private_speaker_review_workspace as submission

from cinegraph.domain.enums.enum import SpeakerReviewRunStatus


@dataclass(frozen=True)
class State:
    run_id: str = "speaker-review-0123456789abcdef"
    status: SpeakerReviewRunStatus = SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED
    actual_primary_cost_usd: float = 0.0000001
    actual_adjudication_cost_usd: float = 0.0000001
    actual_final_review_cost_usd: float = 0.0
    maximum_cost_usd: float = 5.0
    final_review_part_count: int = 1
    final_review_completed_part_count: int = 0


class Gateway:
    def __init__(self) -> None:
        self.retrieved: list[str] = []
        self.downloaded: list[str] = []
        self.submissions = 0

    def retrieve(self, batch_id: str) -> object:
        self.retrieved.append(batch_id)
        return object()

    def download_file(self, file_id: str) -> str:
        self.downloaded.append(file_id)
        return "provider output\n"

    def submit(self, *_: object, **__: object) -> object:
        self.submissions += 1
        raise AssertionError("observation must not submit")


class Workflow:
    def __init__(self, gateway: Gateway, result: State | BaseException) -> None:
        self.gateway = gateway
        self.result = result

    def observe_final_review_part_one(
        self, _run: Path, *, verified_run_state: State
    ) -> tuple[Path, State]:
        self.gateway.retrieve("final-batch")
        if isinstance(self.result, BaseException):
            raise self.result
        return Path(_run), self.result


def _environment(*, cap: int = 5_000_000) -> dict[str, str]:
    return {
        contract.ENV_ARCHIVE_SHA256: "a" * 64,
        contract.ENV_RUN_ID: "speaker-review-0123456789abcdef",
        contract.ENV_AUTHORIZATION_ID: "12345678-1234-4234-8234-123456789abc",
        contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: str(cap),
        contract.ENV_EXPECTED_SUBMISSION_RECEIPT_SHA256: "b" * 64,
    }


def _install_worker_mocks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    state: State,
    result: State | BaseException,
    estimate: int = 1,
) -> tuple[Gateway, list[Path], list[Path]]:
    run = tmp_path / state.run_id
    gateway = Gateway()
    secret_reads: list[Path] = []
    run_roots: list[Path] = []
    monkeypatch.setattr(
        submission,
        "_run_directory",
        lambda _run_id, review_root: run_roots.append(review_root) or run,
    )
    monkeypatch.setattr(
        worker,
        "load_validated_run_state",
        lambda _run, _configuration: (run, state),
    )
    monkeypatch.setattr(submission, "_inventory", lambda _run: ({"state": b"unchanged"}, state))
    monkeypatch.setattr(worker, "_bound_inventory", lambda *_: (b"request\n", estimate))
    monkeypatch.setattr(
        worker,
        "read_stable_openai_secret",
        lambda path: secret_reads.append(path) or "test-secret",
    )

    import openai

    from cinegraph.adapters.llm import openai_speaker_review_batch_gateway

    monkeypatch.setattr(openai, "OpenAI", lambda *, api_key: object())
    monkeypatch.setattr(
        openai_speaker_review_batch_gateway,
        "OpenAISpeakerReviewBatchGateway",
        lambda _client, _configuration: gateway,
    )
    monkeypatch.setattr(worker, "_workflow", lambda received: Workflow(received, result))
    return gateway, secret_reads, run_roots


def test_fractional_micro_costs_are_rounded_up_individually() -> None:
    aggregate = worker._aggregate(State(), {
        "maximum_authorized_cost_microusd": 5_000_000,
    }, 1, "waiting")

    assert aggregate["actual_primary_cost_microusd"] == 1
    assert aggregate["actual_adjudication_cost_microusd"] == 1


def test_ambient_secret_rejected_before_secret_read_or_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gateway, secret_reads, _ = _install_worker_mocks(
        monkeypatch, tmp_path, state=State(), result=State()
    )
    environment = _environment()
    environment["OPENAI_API_KEY"] = "ambient-secret"

    with pytest.raises(worker.FinalReviewObservationWorkerError, match="environment"):
        worker.observe_final_review_part_one(
            environment=environment, review_root=tmp_path
        )

    assert secret_reads == []
    assert gateway.retrieved == []
    assert gateway.submissions == 0


def test_binding_failure_happens_before_secret_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gateway, secret_reads, _ = _install_worker_mocks(
        monkeypatch, tmp_path, state=State(), result=State()
    )
    monkeypatch.setattr(
        worker,
        "_bound_inventory",
        lambda *_: (_ for _ in ()).throw(worker.FinalReviewObservationWorkerError("binding")),
    )

    with pytest.raises(worker.FinalReviewObservationWorkerError, match="binding"):
        worker.observe_final_review_part_one(
            environment=_environment(), review_root=tmp_path
        )

    assert secret_reads == []
    assert gateway.retrieved == []


def test_cost_cap_failure_happens_before_secret_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    costly = replace(State(), actual_primary_cost_usd=0.000002)
    gateway, secret_reads, _ = _install_worker_mocks(
        monkeypatch, tmp_path, state=costly, result=costly, estimate=0
    )

    with pytest.raises(worker.FinalReviewObservationWorkerError, match="exceeds authorization"):
        worker.observe_final_review_part_one(
            environment=_environment(cap=1), review_root=tmp_path
        )

    assert secret_reads == []
    assert gateway.retrieved == []


def test_state_cost_cap_failure_happens_before_secret_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    capped = replace(State(), maximum_cost_usd=0.000002)
    gateway, secret_reads, _ = _install_worker_mocks(
        monkeypatch, tmp_path, state=capped, result=capped, estimate=1
    )

    with pytest.raises(worker.FinalReviewObservationWorkerError, match="exceeds authorization"):
        worker.observe_final_review_part_one(
            environment=_environment(), review_root=tmp_path
        )

    assert secret_reads == []
    assert gateway.retrieved == []


def test_success_observes_once_and_never_submits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    observed = replace(State(), final_review_completed_part_count=1)
    gateway, secret_reads, run_roots = _install_worker_mocks(
        monkeypatch, tmp_path, state=State(), result=observed
    )

    result = worker.observe_final_review_part_one(
        environment=_environment(), review_root=tmp_path
    )

    assert result["status"] == "observed"
    assert gateway.retrieved == ["final-batch"]
    assert gateway.submissions == 0
    assert secret_reads == [worker.OPENAI_SECRET_PATH]
    assert run_roots == [tmp_path]


def test_waiting_observation_does_not_mutate_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gateway, _, _ = _install_worker_mocks(
        monkeypatch, tmp_path, state=State(), result=State()
    )

    result = worker.observe_final_review_part_one(
        environment=_environment(), review_root=tmp_path
    )

    assert result["status"] == "waiting"
    assert gateway.retrieved == ["final-batch"]
    assert gateway.submissions == 0
    assert list(tmp_path.iterdir()) == []


def test_terminal_batch_failure_returns_failed_aggregate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    submitted = State()
    failed = replace(submitted, status=SpeakerReviewRunStatus.FAILED)
    gateway, _, _ = _install_worker_mocks(
        monkeypatch,
        tmp_path,
        state=submitted,
        result=RuntimeError("OpenAI Batch final-batch ended with status failed"),
    )
    states = iter(((tmp_path / submitted.run_id, submitted), (tmp_path / submitted.run_id, failed)))
    monkeypatch.setattr(
        worker,
        "load_validated_run_state",
        lambda *_: next(states),
    )

    result = worker.observe_final_review_part_one(
        environment=_environment(), review_root=tmp_path
    )

    assert result["status"] == "failed"
    assert gateway.retrieved == ["final-batch"]
    assert gateway.submissions == 0


def test_main_emits_only_generic_error(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def fail() -> dict[str, object]:
        raise worker.FinalReviewObservationWorkerError("private provider detail")

    monkeypatch.setattr(worker, "observe_final_review_part_one", fail)

    assert worker.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error=speaker_review_final_review_observation_failed\n"
