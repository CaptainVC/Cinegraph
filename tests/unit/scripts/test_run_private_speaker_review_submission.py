from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts import private_speaker_review_submission_contract as contract
from scripts import private_speaker_review_submission_host_contract as host_contract
from scripts import run_private_speaker_review as phase60_processor
from scripts import run_private_speaker_review_submission as processor

RUN_ID = "speaker-review-0123456789abcdef"
AUTHORIZATION_ID = "123e4567-e89b-42d3-a456-426614174000"
DIGEST = "a" * 64


def _request(*, authorization_id: str = AUTHORIZATION_ID) -> dict[str, object]:
    return {
        "archive_sha256": DIGEST,
        "authorization_id": authorization_id,
        "maximum_authorized_cost_microusd": 5_000_000,
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "schema_version": contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
    }


def _state(*, status: str = "prepared") -> dict[str, object]:
    submitted = status == "primary_submitted"
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
        "primary_completed_part_count": 0,
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


def _aggregate(status: str = "submitted") -> dict[str, object]:
    return {
        "estimated_primary_cost_microusd": 250_000,
        "operation": contract.OPERATION,
        "primary_part_count": 1,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "season_number": contract.SEASON_NUMBER,
        "status": status,
        "submitted_part_count": 1 if status != "reconciliation_required" else 0,
    }


def _write_private(path: Path, content: bytes) -> None:
    path.write_bytes(content)
    if os.name == "posix":
        path.chmod(0o600)


def _configure_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    root = tmp_path / "speaker-review"
    receipts = root / "receipts"
    authorization = root / "authorization"
    submission_receipts = root / "submission-receipts"
    review_runs = root.parent / "review-runs"
    container_root = review_runs / f"sha256-{DIGEST}"
    worker_review_runs = container_root / "review-runs"
    for directory in (
        root,
        receipts,
        authorization,
        submission_receipts,
        review_runs,
        container_root,
        worker_review_runs,
    ):
        directory.mkdir(parents=True)
        if os.name == "posix":
            directory.chmod(0o700)
    run = worker_review_runs / RUN_ID
    run.mkdir()
    if os.name == "posix":
        run.chmod(0o700)
    monkeypatch.setattr(processor, "SPEAKER_REVIEW_ROOT", root)
    monkeypatch.setattr(processor, "PREPARATION_RECEIPTS_ROOT", receipts)
    monkeypatch.setattr(processor, "AUTHORIZATION_ROOT", authorization)
    monkeypatch.setattr(processor, "SUBMISSION_RECEIPTS_ROOT", submission_receipts)
    monkeypatch.setattr(processor, "REVIEW_RUNS_ROOT", review_runs)
    monkeypatch.setattr(
        processor,
        "_active_runtime_binding",
        lambda: (
            "e" * 40,
            "ghcr.io/cinegraph@sha256:" + "d" * 64,
            "c" * 64,
        ),
    )
    if os.name == "posix":
        monkeypatch.setattr(processor, "ROOT_UID", os.getuid())
        monkeypatch.setattr(processor, "ROOT_GID", os.getgid())
        monkeypatch.setattr(processor, "WORKER_UID", os.getuid())
        monkeypatch.setattr(processor, "WORKER_GID", os.getgid())

    base = {
        "candidates.jsonl": b'{"candidate_id":"private"}\n',
        "primary-part-0001-requests.jsonl": b'{"custom_id":"one"}\n',
        "source-manifest.json": b'{"schema_version":1,"sources":{}}\n',
    }
    state_raw = json.dumps(_state(), ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n"
    base["run-state.json"] = state_raw
    for name, content in base.items():
        _write_private(run / name, content)
    artifact_digest = processor._set_digest(base)
    prep_result = {
        "candidate_count": 2,
        "estimated_primary_cost_usd": 0.25,
        "file_count": 3,
        "operation": "prepare",
        "primary_part_count": 1,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "season_number": contract.SEASON_NUMBER,
        "status": "prepared",
        "total_bytes": sum(len(value) for value in base.values()),
    }
    receipt = {
        "archive_sha256": DIGEST,
        "artifact_file_count": len(base),
        "artifact_set_sha256": artifact_digest,
        "catalogue_sha256": "b" * 64,
        "configuration_sha256": "c" * 64,
        "image_reference": "ghcr.io/cinegraph@sha256:" + "d" * 64,
        "release_sha": "e" * 40,
        "result": prep_result,
        "schema_version": 1,
    }
    _write_private(receipts / f"sha256-{DIGEST}.json", processor._canonical_json(receipt))
    _write_private(
        authorization / f"{AUTHORIZATION_ID}.json",
        contract.canonical_json(_request()),
    )
    return {"root": root, "run": run, "receipts": submission_receipts}


def _completed_journals(run: Path, *, state: dict[str, object] | None = None) -> None:
    request_hash = processor._sha256((run / "primary-part-0001-requests.jsonl").read_bytes())
    binding = {
        "batch_endpoint": "/v1/responses",
        "completion_window": "24h",
        "part": 1,
        "prompt_version": "speaker-review-v1",
        "request_sha256": request_hash,
        "run_id": RUN_ID,
        "schema_version": 1,
        "stage": "primary",
    }
    _write_private(
        run / processor._WORKFLOW_INTENT_FILENAME,
        processor._canonical_json({"binding": binding, "status": "intent"}),
    )
    _write_private(
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
    if state is not None:
        _write_private(
            run / "run-state.json",
            json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n",
        )


def _worker_success(request: dict[str, object], run: Path) -> dict[str, object]:
    del request
    _completed_journals(run, state=_state(status="primary_submitted"))
    return _aggregate()


def test_isolated_coordinator_constants_match_central_host_contract() -> None:
    assert processor.SPEAKER_REVIEW_ROOT == host_contract.SPEAKER_REVIEW_ROOT
    assert processor.PREPARATION_RECEIPTS_ROOT == host_contract.SPEAKER_REVIEW_ROOT / "receipts"
    assert processor.REVIEW_RUNS_ROOT == host_contract.SPEAKER_REVIEW_RUNS_ROOT
    assert processor.AUTHORIZATION_ROOT == host_contract.REVIEW_AUTHORIZATION_ROOT
    assert processor.SUBMISSION_RECEIPTS_ROOT == host_contract.REVIEW_SUBMISSION_RECEIPTS_ROOT
    assert processor.DEV_ENV_FILE == host_contract.ENV_FILE
    assert processor.CONTAINER_NAME == host_contract.CONTAINER_NAME
    assert (processor.WORKER_UID, processor.WORKER_GID) == (
        host_contract.UID_IN_CONTAINER,
        host_contract.GID_IN_CONTAINER,
    )


def test_configuration_hash_matches_phase60_algorithm() -> None:
    repository = Path.cwd()
    assert processor._configuration_sha256(repository) == phase60_processor._configuration_sha256(
        repository
    )


def test_release_image_reference_matches_exact_root_environment_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = tmp_path / ("e" * 40)
    release.mkdir()
    env_file = tmp_path / "dev.env"
    _write_private(
        env_file,
        (
            "CINEGRAPH_ENVIRONMENT=development\n"
            "CINEGRAPH_IMAGE=ghcr.io/captainvc/cinegraph\n"
            f"CINEGRAPH_IMAGE_DIGEST=sha256:{'d' * 64}\n"
            f"CINEGRAPH_RELEASE_SHA={'e' * 40}\n"
            "OPENAI_API_KEY=not-read-by-runtime-binding\n"
        ).encode(),
    )
    monkeypatch.setattr(processor, "RELEASE_ROOT", release)
    monkeypatch.setattr(processor, "DEV_ENV_FILE", env_file)
    if os.name == "posix":
        monkeypatch.setattr(processor, "ROOT_UID", os.getuid())
        monkeypatch.setattr(processor, "ROOT_GID", os.getgid())

    assert processor._release_image_reference() == (
        "ghcr.io/captainvc/cinegraph@sha256:" + "d" * 64
    )


def test_first_submission_binds_authorization_and_replay_never_runs_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _configure_fixture(tmp_path, monkeypatch)
    calls: list[str] = []

    def worker(request: dict[str, object], review_runs: Path) -> dict[str, object]:
        calls.append(str(review_runs))
        return _worker_success(request, fixture["run"])

    monkeypatch.setattr(processor, "_run_worker", worker)
    first = processor.process_request(_request())
    assert first["status"] == "submitted"
    assert len(calls) == 1
    second = processor.process_request(_request())
    assert second["status"] == "already_submitted"
    assert len(calls) == 1
    assert len(list(fixture["receipts"].iterdir())) == 2


@pytest.mark.parametrize(
    "binding",
    [
        ("f" * 40, "ghcr.io/cinegraph@sha256:" + "d" * 64, "c" * 64),
        ("e" * 40, "ghcr.io/cinegraph@sha256:" + "f" * 64, "c" * 64),
        ("e" * 40, "ghcr.io/cinegraph@sha256:" + "d" * 64, "f" * 64),
    ],
)
def test_preparation_receipt_must_match_active_runtime_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    binding: tuple[str, str, str],
) -> None:
    _configure_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(processor, "_active_runtime_binding", lambda: binding)
    monkeypatch.setattr(
        processor,
        "_run_worker",
        lambda *_: pytest.fail("runtime mismatch must stop before provider worker"),
    )
    with pytest.raises(processor.SpeakerReviewSubmissionProcessingError, match="runtime"):
        processor.process_request(_request())


def test_unresolved_workflow_intent_is_reconciliation_required_without_worker_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _configure_fixture(tmp_path, monkeypatch)
    calls: list[str] = []

    def unresolved(request: dict[str, object], review_runs: Path) -> dict[str, object]:
        del request, review_runs
        calls.append("provider-boundary")
        request_hash = processor._sha256(
            (fixture["run"] / "primary-part-0001-requests.jsonl").read_bytes()
        )
        binding = {
            "batch_endpoint": "/v1/responses",
            "completion_window": "24h",
            "part": 1,
            "prompt_version": "speaker-review-v1",
            "request_sha256": request_hash,
            "run_id": RUN_ID,
            "schema_version": 1,
            "stage": "primary",
        }
        _write_private(
            fixture["run"] / processor._WORKFLOW_INTENT_FILENAME,
            processor._canonical_json({"binding": binding, "status": "intent"}),
        )
        return _aggregate("reconciliation_required")

    monkeypatch.setattr(processor, "_run_worker", unresolved)
    first = processor.process_request(_request())
    assert first["status"] == "reconciliation_required"
    # The synthetic worker wrote an intent and then raised during post-state
    # validation; the next invocation must stop before invoking it again.
    monkeypatch.setattr(
        processor,
        "_run_worker",
        lambda *_: pytest.fail("unresolved intent must not be retried"),
    )
    result = processor.process_request(_request())
    assert result["status"] == "reconciliation_required"
    assert len(calls) == 1


def test_primary_submitted_state_must_match_completed_journal_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _configure_fixture(tmp_path, monkeypatch)
    _completed_journals(fixture["run"], state=_state(status="primary_submitted"))
    completed_path = fixture["run"] / processor._WORKFLOW_COMPLETED_FILENAME
    completed = json.loads(completed_path.read_text(encoding="utf-8"))
    completed["batch_id"] = "different-batch"
    _write_private(completed_path, processor._canonical_json(completed))
    base_contents = {
        path.name: path.read_bytes()
        for path in fixture["run"].iterdir()
        if path.name
        not in {
            processor._WORKFLOW_INTENT_FILENAME,
            processor._WORKFLOW_COMPLETED_FILENAME,
        }
    }
    journal_contents = {
        path.name: path.read_bytes()
        for path in fixture["run"].iterdir()
        if path.name
        in {
            processor._WORKFLOW_INTENT_FILENAME,
            processor._WORKFLOW_COMPLETED_FILENAME,
        }
    }
    with pytest.raises(processor.SpeakerReviewSubmissionProcessingError, match="state"):
        processor._validate_workflow_journals(
            fixture["run"],
            base_contents,
            journal_contents,
            _state(status="primary_submitted"),
        )


def test_different_authorization_cannot_reuse_root_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _configure_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        processor,
        "_run_worker",
        lambda request, _review_runs: _worker_success(request, fixture["run"]),
    )
    assert processor.process_request(_request())["status"] == "submitted"
    alternate = "123e4567-e89b-42d3-a456-426614174001"
    _write_private(
        fixture["root"] / "authorization" / f"{alternate}.json",
        contract.canonical_json(_request(authorization_id=alternate)),
    )
    with pytest.raises(processor.SpeakerReviewSubmissionProcessingError):
        processor.process_request(_request(authorization_id=alternate))


def test_compose_worker_has_only_review_runs_rw_and_nonsecret_request_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = tmp_path / "review-runs"
    args = processor._worker_arguments(_request(), runs)
    assert f"{runs.as_posix()}:/review-workspace/review-runs:rw" in args
    assert not any("/private-corpus" in value for value in args)
    assert not any("OPENAI_API_KEY" in value for value in args)
    assert processor._safe_compose_environment() == {
        "PATH": "/usr/sbin:/usr/bin",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }


def test_phase60_digest_bound_nested_run_layout_is_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _configure_fixture(tmp_path, monkeypatch)
    assert processor._run_directory(DIGEST, RUN_ID) == fixture["run"]
    with pytest.raises(processor.SpeakerReviewSubmissionProcessingError):
        processor._run_directory("b" * 64, RUN_ID)


def test_entrypoint_is_stdlib_only_and_fail_closed_under_isolated_no_site_mode() -> None:
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "scripts/run_private_speaker_review_submission.py"],
        input=b"",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 2
    assert completed.stdout == b""
    assert completed.stderr == contract.canonical_json(
        {"error": "speaker_review_submission_rejected", "status": "error"}
    )
