from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path

import pytest
from scripts import private_speaker_review_observation_contract as contract
from scripts import run_private_speaker_review_observation as processor

RUN_ID = "speaker-review-0123456789abcdef"
AUTHORIZATION_ID = "123e4567-e89b-42d3-a456-426614174000"
SUBMISSION_AUTHORIZATION_ID = "123e4567-e89b-42d3-a456-426614174001"
DIGEST = "a" * 64


def _request() -> dict[str, object]:
    return {
        "archive_sha256": DIGEST,
        "authorization_id": AUTHORIZATION_ID,
        "maximum_authorized_cost_microusd": 5_000_000,
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "schema_version": contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
    }


def _state(status: str = "primary_submitted", completed: int = 0) -> dict[str, object]:
    submitted = status != "prepared"
    return {
        "schema_version": 5,
        "run_id": RUN_ID,
        "status": status,
        "created_at": "2026-09-07T00:00:00+00:00",
        "updated_at": "2026-09-07T00:00:00+00:00",
        "candidate_count": 2,
        "primary_model": "gpt-5.6-luna",
        "adjudication_model": "gpt-5.6-terra",
        "prompt_version": "speaker-review-v1",
        "maximum_cost_usd": 5.0,
        "estimated_primary_cost_usd": 0.25,
        "actual_primary_cost_usd": 0.0,
        "actual_adjudication_cost_usd": 0.0,
        "final_review_model": "gpt-5.6-sol",
        "actual_final_review_cost_usd": 0.0,
        "primary_batch_id": "batch-private" if submitted else None,
        "primary_input_file_id": "file-private" if submitted else None,
        "adjudication_batch_id": None,
        "adjudication_input_file_id": None,
        "primary_part_count": 1,
        "primary_completed_part_count": completed,
        "primary_batch_ids": ["batch-private"] if submitted else [],
        "primary_input_file_ids": ["file-private"] if submitted else [],
        "adjudication_part_count": 0,
        "adjudication_completed_part_count": 0,
        "adjudication_batch_ids": [],
        "adjudication_input_file_ids": [],
        "final_review_part_count": 0,
        "final_review_completed_part_count": 0,
        "final_review_batch_ids": [],
        "final_review_input_file_ids": [],
        "final_review_batch_id": None,
        "final_review_input_file_id": None,
        "final_review_retry_count": 0,
        "accepted_by_consensus": 0,
        "accepted_by_adjudication": 0,
        "accepted_by_final_review": 0,
        "accepted_by_human": 0,
        "needs_human": 0,
        "actual_total_cost_usd": 0.0,
    }


def _write(path: Path, value: bytes) -> None:
    path.write_bytes(value)
    if os.name == "posix":
        path.chmod(0o600)


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    root = tmp_path / "speaker-review"
    prep = root / "receipts"
    auth = root / "authorization"
    submissions = root / "submission-receipts"
    observations = root / "observation-receipts"
    runs = root.parent / "review-runs"
    object_root = runs / f"sha256-{DIGEST}"
    review_runs = object_root / "review-runs"
    run = review_runs / RUN_ID
    for directory in (
        root,
        prep,
        auth,
        submissions,
        observations,
        runs,
        object_root,
        review_runs,
        run,
    ):
        directory.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            directory.chmod(0o700)
    for module in (processor,):
        monkeypatch.setattr(module, "SPEAKER_REVIEW_ROOT", root)
        monkeypatch.setattr(module, "PREPARATION_RECEIPTS_ROOT", prep)
        monkeypatch.setattr(module, "AUTHORIZATION_ROOT", auth)
        monkeypatch.setattr(module, "SUBMISSION_RECEIPTS_ROOT", submissions)
        monkeypatch.setattr(module, "OBSERVATION_RECEIPTS_ROOT", observations)
        monkeypatch.setattr(module, "REVIEW_RUNS_ROOT", runs)
    if os.name == "posix":
        monkeypatch.setattr(processor, "ROOT_UID", os.getuid())
        monkeypatch.setattr(processor, "ROOT_GID", os.getgid())
        monkeypatch.setattr(processor, "WORKER_UID", os.getuid())
        monkeypatch.setattr(processor, "WORKER_GID", os.getgid())
    monkeypatch.setattr(
        processor.submission,
        "_active_runtime_binding",
        lambda: ("e" * 40, "ghcr.io/cinegraph@sha256:" + "d" * 64, "c" * 64),
    )

    base = {
        "candidates.jsonl": b'{"candidate_id":"private"}\n',
        "primary-part-0001-requests.jsonl": b'{"custom_id":"one"}\n',
        "source-manifest.json": b'{"schema_version":1,"sources":{}}\n',
    }
    _write(run / "run-state.json", processor._canonical_json(_state()))
    for name, value in base.items():
        _write(run / name, value)
    base["run-state.json"] = (run / "run-state.json").read_bytes()
    request_sha = processor._sha256(base["primary-part-0001-requests.jsonl"])
    binding = {
        "batch_endpoint": "/v1/responses",
        "completion_window": "24h",
        "part": 1,
        "prompt_version": "speaker-review-v1",
        "request_sha256": request_sha,
        "run_id": RUN_ID,
        "schema_version": 1,
        "stage": "primary",
    }
    _write(
        run / processor._WORKFLOW_INTENT_FILENAME,
        processor._canonical_json({"binding": binding, "status": "intent"}),
    )
    _write(
        run / processor._WORKFLOW_COMPLETED_FILENAME,
        processor._canonical_json(
            {
                "batch_id": "batch-private",
                "binding": binding,
                "input_file_id": "file-private",
                "status": "validating",
            }
        ),
    )
    journal_contents = {
        processor._WORKFLOW_INTENT_FILENAME: (
            run / processor._WORKFLOW_INTENT_FILENAME
        ).read_bytes(),
        processor._WORKFLOW_COMPLETED_FILENAME: (
            run / processor._WORKFLOW_COMPLETED_FILENAME
        ).read_bytes(),
    }
    pre_submission_contents = {**base, **journal_contents}
    base_hashes = {name: processor._sha256(value) for name, value in base.items()}
    artifact_set = processor._set_digest(base)
    prep_result = {
        "candidate_count": 2,
        "estimated_primary_cost_usd": 0.25,
        "file_count": len(base),
        "operation": "prepare",
        "primary_part_count": 1,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "season_number": contract.SEASON_NUMBER,
        "status": "prepared",
        "total_bytes": sum(len(value) for value in base.values()),
    }
    prep_receipt = {
        "archive_sha256": DIGEST,
        "artifact_file_count": len(base),
        "artifact_set_sha256": artifact_set,
        "catalogue_sha256": "b" * 64,
        "configuration_sha256": "c" * 64,
        "image_reference": "ghcr.io/cinegraph@sha256:" + "d" * 64,
        "release_sha": "e" * 40,
        "result": prep_result,
        "schema_version": 1,
    }
    prep_raw = processor._canonical_json(prep_receipt)
    _write(prep / f"sha256-{DIGEST}.json", prep_raw)
    _write(auth / f"{AUTHORIZATION_ID}.json", contract.canonical_json(_request()))
    submit_request = {
        "archive_sha256": DIGEST,
        "authorization_id": SUBMISSION_AUTHORIZATION_ID,
        "maximum_authorized_cost_microusd": 5_000_000,
        "operation": "submit_primary",
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "schema_version": 1,
        "season_number": 2,
    }
    submit_raw = processor.submission.contract.canonical_json(submit_request)
    _write(auth / f"{SUBMISSION_AUTHORIZATION_ID}.json", submit_raw)
    submission_result = {
        "estimated_primary_cost_microusd": 250_000,
        "operation": "submit_primary",
        "primary_part_count": 1,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "season_number": 2,
        "status": "submitted",
        "submitted_part_count": 1,
    }
    submission_receipt = {
        "archive_sha256": DIGEST,
        "authorization_id": SUBMISSION_AUTHORIZATION_ID,
        "authorization_sha256": processor._sha256(submit_raw),
        "estimated_primary_cost_microusd": 250_000,
        "maximum_authorized_cost_microusd": 5_000_000,
        "operation": "submit_primary",
        "prep_receipt_sha256": processor._sha256(prep_raw),
        "prepared_artifact_file_count": len(base),
        "prepared_artifact_set_sha256": artifact_set,
        "prepared_artifact_hashes": base_hashes,
        "primary_part_count": 1,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "schema_version": 1,
        "season_number": 2,
        "status": "submitted",
        "post_artifact_file_count": len(pre_submission_contents),
        "post_artifact_set_sha256": processor._set_digest(pre_submission_contents),
        "post_journal_file_count": 2,
        "post_journal_set_sha256": processor._set_digest(journal_contents),
        "post_run_state_sha256": base_hashes["run-state.json"],
        "result": submission_result,
    }
    _write(submissions / f"{RUN_ID}.json", processor._canonical_json(submission_receipt))
    return {"run": run, "observations": observations}


def _aggregate(
    status: str, run_status: str = "primary_submitted", completed: int = 0
) -> dict[str, object]:
    return {
        "estimated_primary_cost_microusd": 250_000,
        "operation": contract.OPERATION,
        "primary_completed_part_count": completed,
        "primary_part_count": 1,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "run_status": run_status,
        "season_number": 2,
        "status": status,
    }


def test_first_waiting_observation_keeps_intent_and_second_call_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    calls: list[int] = []

    def waiting_then_observed(*_: object) -> dict[str, object]:
        calls.append(1)
        if len(calls) == 1:
            return _aggregate("waiting")
        state = _state("primary_part_completed", 1)
        _write(fixture["run"] / "run-state.json", processor._canonical_json(state))
        _write(fixture["run"] / "primary-part-0001-output.jsonl", b'{"ok":true}\n')
        return _aggregate("observed", "primary_part_completed", 1)

    monkeypatch.setattr(processor, "_run_worker", waiting_then_observed)
    assert processor.process_request(_request())["status"] == "waiting"
    assert (fixture["observations"] / f"{RUN_ID}.intent.json").exists()
    assert not (fixture["observations"] / f"{RUN_ID}.json").exists()
    assert len(calls) == 1
    assert processor.process_request(_request())["status"] == "observed"
    assert len(calls) == 2


def test_observed_receipt_replays_without_worker_and_binds_nested_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)

    def observed(*_: object) -> dict[str, object]:
        state = _state("primary_part_completed", 1)
        _write(fixture["run"] / "run-state.json", processor._canonical_json(state))
        _write(fixture["run"] / "primary-part-0001-output.jsonl", b'{"ok":true}\n')
        return _aggregate("observed", "primary_part_completed", 1)

    monkeypatch.setattr(processor, "_run_worker", observed)
    assert processor.process_request(_request())["status"] == "observed"
    monkeypatch.setattr(processor, "_run_worker", lambda *_: pytest.fail("replay must not run"))
    assert processor.process_request(_request())["status"] == "already_observed"
    receipt = json.loads(
        (fixture["observations"] / f"{RUN_ID}.json").read_text(encoding="utf-8")
    )
    assert receipt["status"] == "observed"
    assert receipt["result"]["status"] == "observed"


def test_crash_after_worker_repairs_terminal_receipt_without_provider_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)

    def crash_after_observe(*_: object) -> dict[str, object]:
        _write(
            fixture["run"] / "run-state.json",
            processor._canonical_json(_state("primary_part_completed", 1)),
        )
        _write(fixture["run"] / "primary-part-0001-output.jsonl", b'{"ok":true}\n')
        raise RuntimeError("simulated coordinator crash")

    monkeypatch.setattr(processor, "_run_worker", crash_after_observe)
    with pytest.raises(RuntimeError, match="crash"):
        processor.process_request(_request())
    monkeypatch.setattr(
        processor,
        "_run_worker",
        lambda *_: pytest.fail("crash repair must not invoke provider worker"),
    )
    assert processor.process_request(_request())["status"] == "observed"


def test_partial_output_after_crash_is_reused_by_the_observer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    calls: list[int] = []

    def crash_then_finish(*_: object) -> dict[str, object]:
        calls.append(1)
        output = fixture["run"] / "primary-part-0001-output.jsonl"
        if len(calls) == 1:
            _write(output, b'{"ok":true}\n')
            raise RuntimeError("simulated crash after output")
        assert output.read_bytes() == b'{"ok":true}\n'
        _write(
            fixture["run"] / "run-state.json",
            processor._canonical_json(_state("primary_part_completed", 1)),
        )
        return _aggregate("observed", "primary_part_completed", 1)

    monkeypatch.setattr(processor, "_run_worker", crash_then_finish)
    with pytest.raises(RuntimeError, match="crash after output"):
        processor.process_request(_request())
    assert processor.process_request(_request())["status"] == "observed"
    assert len(calls) == 2


def test_failed_observation_repairs_and_replays_without_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)

    def failed(*_: object) -> dict[str, object]:
        _write(
            fixture["run"] / "run-state.json",
            processor._canonical_json(_state("failed", 0)),
        )
        _write(fixture["run"] / "terminal-api-errors.jsonl", b'{"error":"terminal"}\n')
        return _aggregate("failed", "failed", 0)

    monkeypatch.setattr(processor, "_run_worker", failed)
    assert processor.process_request(_request())["status"] == "failed"
    monkeypatch.setattr(
        processor,
        "_run_worker",
        lambda *_: pytest.fail("failed replay must not invoke worker"),
    )
    assert processor.process_request(_request())["status"] == "failed"


def test_failed_observation_without_error_artifact_repairs_after_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)

    def crash_after_failure(*_: object) -> dict[str, object]:
        _write(
            fixture["run"] / "run-state.json",
            processor._canonical_json(_state("failed", 0)),
        )
        raise RuntimeError("simulated failure receipt crash")

    monkeypatch.setattr(processor, "_run_worker", crash_after_failure)
    with pytest.raises(RuntimeError, match="receipt crash"):
        processor.process_request(_request())
    monkeypatch.setattr(
        processor,
        "_run_worker",
        lambda *_: pytest.fail("failure repair must not invoke worker"),
    )
    assert processor.process_request(_request())["status"] == "failed"


def test_terminal_error_written_before_state_is_reused_after_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    calls: list[int] = []

    def crash_then_fail(*_: object) -> dict[str, object]:
        calls.append(1)
        terminal = fixture["run"] / "terminal-api-errors.jsonl"
        if len(calls) == 1:
            _write(terminal, b'{"error":"terminal"}\n')
            raise RuntimeError("simulated crash after terminal error")
        assert terminal.read_bytes() == b'{"error":"terminal"}\n'
        _write(
            fixture["run"] / "run-state.json",
            processor._canonical_json(_state("failed", 0)),
        )
        return _aggregate("failed", "failed", 0)

    monkeypatch.setattr(processor, "_run_worker", crash_then_fail)
    with pytest.raises(RuntimeError, match="terminal error"):
        processor.process_request(_request())
    assert processor.process_request(_request())["status"] == "failed"
    assert len(calls) == 2


def test_fresh_intent_rejects_preseeded_observation_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    _write(fixture["run"] / "primary-part-0001-output.jsonl", b'{"forged":true}\n')
    monkeypatch.setattr(
        processor,
        "_run_worker",
        lambda *_: pytest.fail("preseeded evidence must stop before the worker"),
    )
    with pytest.raises(processor.SpeakerReviewObservationProcessingError):
        processor.process_request(_request())
    assert not (fixture["observations"] / f"{RUN_ID}.intent.json").exists()


def test_partial_staging_file_is_repaired_before_intent_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    intent = fixture["observations"] / f"{RUN_ID}.intent.json"
    staging = processor._staging_path(intent)
    _write(staging, b'{"partial"')
    monkeypatch.setattr(processor, "_run_worker", lambda *_: _aggregate("waiting"))

    assert processor.process_request(_request())["status"] == "waiting"
    assert intent.exists()
    assert not staging.exists()
    assert json.loads(intent.read_text(encoding="utf-8"))["status"] == "intent"


def test_linked_publication_is_finished_before_record_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    final = fixture["observations"] / "publication.json"
    staging = processor._staging_path(final)
    payload = {"schema_version": 1, "status": "observed"}
    _write(staging, processor._canonical_json(payload))
    os.link(staging, final)

    assert processor._read_root_record(final) == payload
    assert final.exists()
    assert not staging.exists()


def test_complete_staging_file_is_published_without_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    final = fixture["observations"] / "publication.json"
    staging = processor._staging_path(final)
    payload = {"schema_version": 1, "status": "observed"}
    _write(staging, processor._canonical_json(payload))

    processor._write_once(final, payload)

    assert processor._read_root_record(final) == payload
    assert not staging.exists()


def test_mismatched_staging_and_existing_final_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    final = fixture["observations"] / "publication.json"
    staging = processor._staging_path(final)
    expected = {"schema_version": 1, "status": "observed"}
    _write(staging, processor._canonical_json({**expected, "status": "failed"}))
    with pytest.raises(processor.SpeakerReviewObservationProcessingError, match="conflict"):
        processor._write_once(final, expected)
    assert staging.exists()
    assert not final.exists()

    staging.unlink()
    _write(final, processor._canonical_json({**expected, "status": "failed"}))
    with pytest.raises(processor.SpeakerReviewObservationProcessingError, match="conflict"):
        processor._write_once(final, expected)


def test_crash_after_atomic_link_is_repairable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    final = fixture["observations"] / "publication.json"
    staging = processor._staging_path(final)
    payload = {"schema_version": 1, "status": "observed"}
    real_unlink = os.unlink

    def crash_on_staging_unlink(path: str | os.PathLike[str]) -> None:
        if Path(path) == staging and final.exists():
            raise OSError("simulated crash after link")
        real_unlink(path)

    monkeypatch.setattr(processor.os, "unlink", crash_on_staging_unlink)
    with pytest.raises(processor.SpeakerReviewObservationProcessingError, match="unavailable"):
        processor._write_once(final, payload)
    assert final.exists() and staging.exists()

    monkeypatch.setattr(processor.os, "unlink", real_unlink)
    assert processor._read_root_record(final) == payload
    assert not staging.exists()


def test_snapshot_enforces_aggregate_artifact_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixture(tmp_path, monkeypatch)
    preparation = processor._validate_preparation_receipt(_request())
    monkeypatch.setattr(processor, "RUN_ARTIFACT_TOTAL_MAX_BYTES", 128)

    with pytest.raises(processor.SpeakerReviewObservationProcessingError):
        processor._read_run_snapshot(preparation, expected_hashes=None)


def test_worker_cannot_mutate_a_prepared_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)

    def corrupt(*_: object) -> dict[str, object]:
        _write(fixture["run"] / "candidates.jsonl", b'{"changed":true}\n')
        return _aggregate("waiting")

    monkeypatch.setattr(processor, "_run_worker", corrupt)
    with pytest.raises(processor.SpeakerReviewObservationProcessingError, match="artifacts"):
        processor.process_request(_request())


def test_reconciliation_is_bounded_and_never_writes_final_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        processor,
        "_run_worker",
        lambda *_: _aggregate("reconciliation_required"),
    )
    assert processor.process_request(_request())["status"] == "reconciliation_required"
    assert not (fixture["observations"] / f"{RUN_ID}.json").exists()


def test_submission_post_hashes_are_verified_before_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    path = fixture["run"].parents[3] / "speaker-review" / "submission-receipts" / f"{RUN_ID}.json"
    receipt = json.loads(path.read_text(encoding="utf-8"))
    receipt["post_run_state_sha256"] = "f" * 64
    _write(path, processor._canonical_json(receipt))
    monkeypatch.setattr(
        processor,
        "_run_worker",
        lambda *_: pytest.fail("invalid submission receipt must stop worker"),
    )
    with pytest.raises(processor.SpeakerReviewObservationProcessingError, match="submission"):
        processor.process_request(_request())


def test_worker_arguments_use_observation_profile_and_no_secret_env() -> None:
    arguments = processor._worker_arguments(_request(), Path("/review-runs"))
    assert processor.COMPOSE_PROFILE in arguments
    assert processor.COMPOSE_SERVICE in arguments
    assert not any("OPENAI_API_KEY" in argument for argument in arguments)
    assert "/review-workspace/review-runs" in " ".join(arguments)


def test_cleanup_identity_binds_compose_labels_image_and_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    review_runs = Path("/private/review-runs")
    image = "ghcr.io/captainvc/cinegraph@sha256:" + "d" * 64
    header = [
        f"/{processor.CONTAINER_NAME}",
        processor.COMPOSE_SERVICE,
        "True",
        os.fspath(processor.COMPOSE_PATH),
        os.fspath(processor.RELEASE_ROOT),
        image,
        f"{review_runs.as_posix()}|{processor.WORKER_MOUNT}|true",
    ]
    monkeypatch.setattr(processor, "_release_image_reference", lambda: image)
    monkeypatch.setattr(
        processor.subprocess,
        "run",
        lambda *_, **__: subprocess.CompletedProcess([], 0, ("\n".join(header) + "\n").encode(), b""),
    )
    assert processor._container_identity_is_exact(review_runs)
    header[1] = "another-service"
    assert not processor._container_identity_is_exact(review_runs)


@pytest.mark.parametrize(
    ("stdout", "stderr"),
    [
        (b"x" * (contract.OUTPUT_MAX_BYTES + 1), b""),
        (contract.canonical_json(_aggregate("waiting")), b"private provider detail\n"),
    ],
)
def test_worker_rejects_oversized_output_or_any_stderr(
    monkeypatch: pytest.MonkeyPatch, stdout: bytes, stderr: bytes
) -> None:
    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = io.BytesIO(stdout)
            self.stderr = io.BytesIO(stderr)

        def wait(self, timeout: int | None = None) -> int:
            del timeout
            return 0

        def poll(self) -> int:
            return 0

    monkeypatch.setattr(processor, "_cleanup_compose_worker", lambda _: None)
    monkeypatch.setattr(processor.subprocess, "Popen", lambda *_, **__: FakeProcess())
    with pytest.raises(processor.SpeakerReviewObservationProcessingError, match="worker failed"):
        processor._run_worker(_request(), Path("/private/review-runs"))


def test_worker_timeout_terminates_and_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProcess:
        pid = 123

        def __init__(self) -> None:
            self.stdout = io.BytesIO(b"")
            self.stderr = io.BytesIO(b"")
            self.running = True
            self.waits = 0

        def wait(self, timeout: int | None = None) -> int:
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("worker", timeout)
            self.running = False
            return -9

        def poll(self) -> int | None:
            return None if self.running else -9

        def kill(self) -> None:
            self.running = False

    fake = FakeProcess()
    monkeypatch.setattr(processor, "_cleanup_compose_worker", lambda _: None)
    monkeypatch.setattr(processor.subprocess, "Popen", lambda *_, **__: fake)
    monkeypatch.setattr(processor.os, "name", "nt")
    with pytest.raises(processor.SpeakerReviewObservationProcessingError, match="worker failed"):
        processor._run_worker(_request(), Path("/private/review-runs"))
    assert not fake.running


def test_duplicate_root_record_keys_are_rejected() -> None:
    with pytest.raises(processor.SpeakerReviewObservationProcessingError):
        processor._decode_json(b'{"a":1,"a":2}\n', canonical=True)


def test_entrypoint_isolated_and_privacy_safe() -> None:
    source = Path(processor.__file__).read_text(encoding="utf-8")
    assert "from cinegraph" not in source
    assert "OPENAI_API_KEY" not in source
