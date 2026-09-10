from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from scripts import private_speaker_review_next_primary_observation_contract as contract
from scripts import run_private_speaker_review_next_primary_observation as processor

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


def _state(*, status: str = "primary_submitted", completed: int = 1) -> dict[str, object]:
    return {
        "immutable": "bound",
        "primary_completed_part_count": completed,
        "primary_part_count": 2,
        "run_id": RUN_ID,
        "status": status,
        "updated_at": "2026-09-10T12:00:00+00:00",
    }


def _contents(*, observed: bool = False, drift: bool = False) -> dict[str, bytes]:
    state = _state(
        status="primary_part_completed" if observed else "primary_submitted",
        completed=2 if observed else 1,
    )
    if observed:
        state["updated_at"] = "2026-09-10T12:01:00+00:00"
    contents = {
        "candidates.jsonl": b'{"candidate":"one"}\n',
        "source-manifest.json": b'{"source":"bound"}\n',
        "primary-part-0001-requests.jsonl": b'{"custom_id":"part-1"}\n',
        "primary-part-0002-requests.jsonl": b'{"custom_id":"part-2"}\n',
        ".primary-part-0001-submission-intent.json": b'{"part":1,"status":"intent"}\n',
        ".primary-part-0001-submission-completed.json": b'{"part":1,"status":"done"}\n',
        ".primary-part-0002-submission-intent.json": b'{"part":2,"status":"intent"}\n',
        ".primary-part-0002-submission-completed.json": b'{"part":2,"status":"done"}\n',
        "primary-part-0001-output.jsonl": b'{"response":"part-1"}\n',
        "run-state.json": processor._canonical(state),
    }
    if drift:
        contents["candidates.jsonl"] = b'{"candidate":"changed"}\n'
    if observed:
        contents["primary-part-0002-output.jsonl"] = b'{"response":"part-2"}\n'
    return contents


def _prep() -> dict[str, object]:
    return {
        "config_sha": "c" * 64,
        "estimated": 250_000,
        "image": IMAGE,
        "receipt": {"artifact_set_sha256": "d" * 64},
        "receipt_sha": "e" * 64,
        "release_sha": "8" * 40,
        "result": {"primary_part_count": 2, "run_id": RUN_ID},
    }


def _worker_result(status: str) -> dict[str, object]:
    terminal = status == "observed"
    return {
        "estimated_primary_cost_microusd": 250_000,
        "operation": contract.OPERATION,
        "primary_completed_part_count": 2 if terminal else 1,
        "primary_part_count": 2,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "run_status": "primary_part_completed" if terminal else "primary_submitted",
        "season_number": contract.SEASON_NUMBER,
        "status": status,
    }


def _install_process_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inventories: list[dict[str, bytes]],
) -> tuple[Path, Path]:
    run = tmp_path / "review-runs" / RUN_ID
    run.mkdir(parents=True)
    receipts = tmp_path / "observation-receipts"
    receipts.mkdir()
    receipts.chmod(0o700)
    sequence = iter(inventories)
    monkeypatch.setattr(processor.observation, "OBSERVATION_RECEIPTS_ROOT", receipts)
    if os.name == "posix":
        monkeypatch.setattr(processor.observation, "ROOT_UID", os.getuid())
        monkeypatch.setattr(processor.observation, "ROOT_GID", os.getgid())
    monkeypatch.setattr(processor, "_validate_authorization", lambda _: "1" * 64)
    monkeypatch.setattr(processor.submit, "_validate_preparation", lambda _: (_prep(), "e" * 64))
    monkeypatch.setattr(processor, "_run_directory", lambda _: run)
    monkeypatch.setattr(
        processor,
        "_inventory",
        lambda *_: (
            (current := next(sequence)),
            json.loads(current["run-state.json"]),
        ),
    )
    monkeypatch.setattr(processor, "_validate_pre_state", lambda *_: None)
    monkeypatch.setattr(processor, "_validate_part2_journals", lambda *_: None)
    monkeypatch.setattr(
        processor.submit,
        "_validate_prior_evidence",
        lambda *_: ("2" * 64, "3" * 64, "4" * 64, "5" * 64, "6" * 64, "7" * 64),
    )
    monkeypatch.setattr(
        processor, "_validate_next_receipt", lambda *_args, **_kwargs: ("a" * 64, "b" * 64)
    )
    monkeypatch.setattr(
        processor, "_validate_predecessors_from_intent", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(processor.submit, "_active_binding", lambda: ("8" * 40, IMAGE, "c" * 64))
    return run, receipts


def test_process_observes_part_two_once_and_replays_without_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before, after = _contents(), _contents(observed=True)
    _, receipts = _install_process_fixture(tmp_path, monkeypatch, [before, after])
    calls = 0

    def run_worker(*_: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return _worker_result("observed")

    monkeypatch.setattr(processor, "_run_worker", run_worker)
    result = processor.process_request(_request())

    assert result == _worker_result("observed")
    assert calls == 1
    intent = receipts / f"{RUN_ID}.part-0002.intent.json"
    receipt = receipts / f"{RUN_ID}.part-0002.json"
    assert intent.exists() and receipt.exists()
    assert "part-2" not in receipt.read_text(encoding="utf-8")

    monkeypatch.setattr(
        processor,
        "_inventory",
        lambda *_: (after, json.loads(after["run-state.json"])),
    )
    monkeypatch.setattr(processor, "_run_worker", lambda *_: pytest.fail("provider replay"))

    replay = processor.process_request(_request())
    assert replay == {**_worker_result("observed"), "status": "already_observed"}


def test_waiting_is_receipt_free_and_exact_retry_can_observe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before, after = _contents(), _contents(observed=True)
    _, receipts = _install_process_fixture(tmp_path, monkeypatch, [before, before])
    monkeypatch.setattr(processor, "_run_worker", lambda *_: _worker_result("waiting"))

    assert processor.process_request(_request()) == _worker_result("waiting")
    assert (receipts / f"{RUN_ID}.part-0002.intent.json").exists()
    assert not (receipts / f"{RUN_ID}.part-0002.json").exists()

    sequence = iter([before, after])
    monkeypatch.setattr(
        processor,
        "_inventory",
        lambda *_: (
            (current := next(sequence)),
            json.loads(current["run-state.json"]),
        ),
    )
    monkeypatch.setattr(processor, "_run_worker", lambda *_: _worker_result("observed"))

    assert processor.process_request(_request()) == _worker_result("observed")


def test_existing_intent_rejects_checkpoint_drift_before_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _contents()
    _install_process_fixture(tmp_path, monkeypatch, [before, before])
    monkeypatch.setattr(processor, "_run_worker", lambda *_: _worker_result("waiting"))
    processor.process_request(_request())

    drifted = _contents(drift=True)
    monkeypatch.setattr(
        processor,
        "_inventory",
        lambda *_: (drifted, json.loads(drifted["run-state.json"])),
    )
    monkeypatch.setattr(processor, "_run_worker", lambda *_: pytest.fail("provider on drift"))

    with pytest.raises(processor.NextPrimaryObservationError, match="binding changed"):
        processor.process_request(_request())


def _container_payload(run_parent: Path) -> dict[str, object]:
    return {
        "Name": "/cinegraph-speaker-review-observe-primary",
        "Config": {
            "Cmd": ["python", "scripts/observe_private_speaker_review_workspace.py"],
            "Env": ["CINEGRAPH_ENVIRONMENT=development"],
            "Image": IMAGE,
            "Labels": {
                "com.docker.compose.oneoff": "True",
                "com.docker.compose.project": "cinegraph-dev",
                "com.docker.compose.project.config_files": str(processor.COMPOSE_PATH),
                "com.docker.compose.project.working_dir": str(processor.RELEASE_ROOT),
                "com.docker.compose.service": "corpus-speaker-review-observe-primary",
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
            {"Destination": "/run/secrets/openai_api_key", "RW": False, "Source": "/secret"},
            {"Destination": "/tmp", "RW": True, "Source": ""},
        ],
        "NetworkSettings": {"Networks": {"cinegraph-dev_egress": {}}},
    }


def test_cleanup_identity_is_exact_and_rejects_extra_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _container_payload(tmp_path)
    monkeypatch.setattr(processor.submit.submission, "_release_image_reference", lambda: IMAGE)
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
    payload["Mounts"].pop()  # type: ignore[union-attr]
    payload["Config"]["Env"].append("OPENAI_API_KEY=private")  # type: ignore[index,union-attr]
    assert not processor._container_identity_is_exact(tmp_path)


def test_worker_arguments_bind_the_root_verified_checkpoint(tmp_path: Path) -> None:
    binding = {
        "pre_artifact_set_sha256": "b" * 64,
        "pre_journal_set_sha256": "c" * 64,
        "pre_run_state_sha256": "d" * 64,
        "request_sha256": "e" * 64,
    }
    arguments = processor._worker_args(_request(), tmp_path, binding)
    assert f"{contract.ENV_EXPECTED_PRIMARY_PART_NUMBER}=2" in arguments
    assert f"{contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256}={'d' * 64}" in arguments
    assert f"{contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256}={'b' * 64}" in arguments
    assert f"{contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256}={'c' * 64}" in arguments
    assert f"{contract.ENV_EXPECTED_REQUEST_SHA256}={'e' * 64}" in arguments
    assert arguments[-1] == processor.host.REVIEW_NEXT_OBSERVATION_COMPOSE_SERVICE


def test_main_never_leaks_private_error_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(processor, "_require_root", lambda: None)
    monkeypatch.setattr(
        processor,
        "_read_request",
        lambda *_: (_ for _ in ()).throw(RuntimeError("sk-private batch-2 /secret/path")),
    )

    assert processor.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error=speaker_review_next_primary_observation_rejected\n"
