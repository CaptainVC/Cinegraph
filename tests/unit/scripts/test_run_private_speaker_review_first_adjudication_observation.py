from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from scripts import private_speaker_review_first_adjudication_observation_contract as contract
from scripts import run_private_speaker_review_first_adjudication_observation as processor

RUN_ID = "speaker-review-0123456789abcdef"
ARCHIVE_SHA256 = "a" * 64
AUTHORIZATION_ID = "123e4567-e89b-42d3-a456-426614174000"
IMAGE = "ghcr.io/captainvc/cinegraph@sha256:" + "9" * 64


def _request() -> dict[str, object]:
    return {
        "archive_sha256": ARCHIVE_SHA256,
        "authorization_id": AUTHORIZATION_ID,
        "maximum_authorized_cost_microusd": 5_000_000,
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "schema_version": contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
    }


def _state(*, status: str = "adjudication_submitted", completed: int = 0) -> dict[str, object]:
    return {
        "actual_primary_cost_usd": 0.1,
        "adjudication_batch_id": "batch-adjudication-1",
        "adjudication_batch_ids": ["batch-adjudication-1"],
        "adjudication_completed_part_count": completed,
        "adjudication_input_file_id": "input-adjudication-1",
        "adjudication_input_file_ids": ["input-adjudication-1"],
        "adjudication_part_count": 2,
        "estimated_adjudication_cost_usd": 0.25,
        "immutable": "bound",
        "run_id": RUN_ID,
        "status": status,
        "updated_at": "2026-09-13T00:00:00+00:00",
    }


def _contents(*, status: str = "adjudication_submitted") -> dict[str, bytes]:
    completed = 1 if status == "adjudication_part_completed" else 0
    state = _state(status=status, completed=completed)
    if status != "adjudication_submitted":
        state["updated_at"] = "2026-09-13T00:01:00+00:00"
    value = {
        processor.STATE: processor._canonical(state),
        processor.REQUEST: b'{"custom_id":"adjudication-1"}\n',
        "candidates.jsonl": b"{}\n",
        "source-manifest.json": b"{}\n",
        processor.SUBMISSION_INTENT: b'{"status":"intent"}\n',
        processor.SUBMISSION_COMPLETED: b'{"status":"completed"}\n',
    }
    if status == "adjudication_part_completed":
        value["adjudication-part-0001-output.jsonl"] = b"{}\n"
    return value


def _worker_result(status: str) -> dict[str, object]:
    completed = 1 if status == "observed" else 0
    run_status = (
        "adjudication_part_completed"
        if status == "observed"
        else "failed"
        if status == "failed"
        else "adjudication_submitted"
    )
    return {
        "actual_primary_cost_microusd": 100_000,
        "adjudication_completed_part_count": completed,
        "adjudication_part_count": 2,
        "estimated_adjudication_cost_microusd": 250_000,
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "run_status": run_status,
        "season_number": contract.SEASON_NUMBER,
        "status": status,
    }


def _install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inventories: list[dict[str, bytes]],
) -> Path:
    run = tmp_path / RUN_ID
    run.mkdir()
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    sequence = iter(inventories)
    preparation = {
        "config_sha": "c" * 64,
        "image": IMAGE,
        "release_sha": "8" * 40,
    }
    monkeypatch.setattr(processor, "OBSERVATION_RECEIPTS_ROOT", receipts)
    monkeypatch.setattr(processor, "_directory", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(processor, "_validate_authorization", lambda _: "1" * 64)
    monkeypatch.setattr(processor, "_run_directory", lambda _: run)
    monkeypatch.setattr(
        processor,
        "_inventory",
        lambda *_: (
            (current := next(sequence)),
            json.loads(current[processor.STATE]),
        ),
    )
    monkeypatch.setattr(
        processor,
        "_validate_submission_predecessor",
        lambda *_: (preparation, "2" * 64, "3" * 64, "4" * 64, 250_000),
    )
    monkeypatch.setattr(processor, "_validate_predecessors_from_intent", lambda *_: None)
    monkeypatch.setattr(
        processor.submit.predecessor,
        "_active_binding",
        lambda: ("8" * 40, IMAGE, "c" * 64),
    )
    monkeypatch.setattr(processor.submit.phase68, "_source_workspace", lambda *_: None)

    def write(path: Path, value: object) -> None:
        path.write_bytes(processor._canonical(value))  # type: ignore[arg-type]

    monkeypatch.setattr(processor, "_write_receipt", write)
    monkeypatch.setattr(
        processor,
        "_read_record",
        lambda path: (
            json.loads(path.read_bytes()),
            processor._sha(path.read_bytes()),
        ),
    )
    return receipts


def test_process_observes_once_and_replays_without_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _contents()
    after = _contents(status="adjudication_part_completed")
    receipts = _install(tmp_path, monkeypatch, [before, after])
    calls = 0

    def worker(*_: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return _worker_result("observed")

    monkeypatch.setattr(processor, "_run_worker", worker)
    assert processor.process_request(_request()) == _worker_result("observed")
    assert calls == 1
    assert (receipts / f"{RUN_ID}.intent.json").exists()
    assert (receipts / f"{RUN_ID}.json").exists()

    monkeypatch.setattr(
        processor,
        "_inventory",
        lambda *_: (after, json.loads(after[processor.STATE])),
    )
    monkeypatch.setattr(processor, "_run_worker", lambda *_: pytest.fail("provider replay"))
    assert processor.process_request(_request()) == {
        **_worker_result("observed"),
        "status": "already_observed",
    }


def test_waiting_keeps_only_the_intent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = _contents()
    receipts = _install(tmp_path, monkeypatch, [before, before])
    monkeypatch.setattr(processor, "_run_worker", lambda *_: _worker_result("waiting"))
    assert processor.process_request(_request()) == _worker_result("waiting")
    assert (receipts / f"{RUN_ID}.intent.json").exists()
    assert not (receipts / f"{RUN_ID}.json").exists()


def test_worker_arguments_bind_all_five_inventory_classes(tmp_path: Path) -> None:
    binding = {
        "pre_artifact_set_sha256": "b" * 64,
        "pre_derived_set_sha256": "c" * 64,
        "pre_journal_set_sha256": "d" * 64,
        "pre_output_set_sha256": "e" * 64,
        "pre_run_state_sha256": "f" * 64,
        "request_sha256": "1" * 64,
    }
    arguments = processor._worker_args(_request(), tmp_path, binding)
    for name, key in (
        (contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256, "pre_artifact_set_sha256"),
        (contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256, "pre_derived_set_sha256"),
        (contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256, "pre_journal_set_sha256"),
        (contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256, "pre_output_set_sha256"),
        (contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256, "pre_run_state_sha256"),
    ):
        assert f"{name}={binding[key]}" in arguments
    assert arguments[-1] == (processor.host.REVIEW_FIRST_ADJUDICATION_OBSERVATION_COMPOSE_SERVICE)


def _container_payload(run_parent: Path) -> dict[str, object]:
    return {
        "Name": "/cinegraph-speaker-review-observe-first-adjudication",
        "Config": {
            "Cmd": [
                "python",
                "scripts/observe_first_private_speaker_review_adjudication_workspace.py",
            ],
            "Env": ["CINEGRAPH_ENVIRONMENT=development"],
            "Image": IMAGE,
            "Labels": {
                "com.docker.compose.oneoff": "True",
                "com.docker.compose.project": "cinegraph-dev",
                "com.docker.compose.project.config_files": str(processor.COMPOSE_PATH),
                "com.docker.compose.project.working_dir": str(processor.RELEASE_ROOT),
                "com.docker.compose.service": ("corpus-speaker-review-observe-first-adjudication"),
            },
            "User": "10002:10002",
            "WorkingDir": "/app",
        },
        "HostConfig": {
            "CapDrop": ["ALL"],
            "PidsLimit": 128,
            "Privileged": False,
            "ReadonlyRootfs": True,
            "SecurityOpt": ["no-new-privileges:true"],
        },
        "Mounts": [
            {
                "Destination": "/review-workspace/review-runs",
                "RW": True,
                "Source": run_parent.as_posix(),
            },
            {
                "Destination": "/run/secrets/openai_api_key",
                "RW": False,
                "Source": "/secret",
            },
            {"Destination": "/tmp", "RW": True, "Source": ""},
        ],
        "NetworkSettings": {"Networks": {"cinegraph-dev_egress": {}}},
    }


def test_cleanup_attestation_rejects_extra_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _container_payload(tmp_path)
    monkeypatch.setattr(
        processor.submit.predecessor,
        "_active_binding",
        lambda: ("8" * 40, IMAGE, "c" * 64),
    )
    monkeypatch.setattr(
        processor.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, json.dumps(payload).encode(), b""
        ),
    )
    assert processor._container_identity_is_exact(tmp_path)
    payload["Mounts"].append(  # type: ignore[union-attr]
        {"Destination": "/host", "RW": True, "Source": "/etc"}
    )
    assert not processor._container_identity_is_exact(tmp_path)


def test_main_never_leaks_private_error_details(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(processor, "_require_root", lambda: None)
    monkeypatch.setattr(
        processor,
        "_read_request",
        lambda *_: (_ for _ in ()).throw(RuntimeError("sk-private batch-private /secret")),
    )
    assert processor.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ("error=speaker_review_first_adjudication_observation_rejected\n")
