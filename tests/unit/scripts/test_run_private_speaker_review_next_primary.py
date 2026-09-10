from __future__ import annotations

import json
import os
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest
from scripts import private_speaker_review_next_primary_submission_contract as contract
from scripts import run_private_speaker_review_next_primary as processor

from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review.workflow import SpeakerReviewRunState

RUN_ID = "speaker-review-0123456789abcdef"
ARCHIVE_SHA256 = "a" * 64
AUTHORIZATION_ID = "123e4567-e89b-42d3-a456-426614174000"
REQUEST_BYTES = b'{"custom_id":"part-2"}\n'


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


def _state(*, submitted: bool = False) -> dict[str, object]:
    batch_ids = ("batch-1", "batch-2") if submitted else ("batch-1",)
    input_ids = ("file-1", "file-2") if submitted else ("file-1",)
    value = SpeakerReviewRunState(
        schema_version=5,
        run_id=RUN_ID,
        status=(
            SpeakerReviewRunStatus.PRIMARY_SUBMITTED
            if submitted
            else SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED
        ),
        created_at="2026-01-01T00:00:00+00:00",
        updated_at=("2026-01-01T00:02:00+00:00" if submitted else "2026-01-01T00:01:00+00:00"),
        candidate_count=2,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        prompt_version="speaker-review-v1",
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=0.25,
        actual_primary_cost_usd=0.0,
        actual_adjudication_cost_usd=0.0,
        primary_part_count=2,
        primary_completed_part_count=1,
        primary_batch_id=batch_ids[-1],
        primary_input_file_id=input_ids[-1],
        primary_batch_ids=batch_ids,
        primary_input_file_ids=input_ids,
    )
    return value.to_dict()


def _journal(part: int, *, completed: bool) -> bytes:
    request = b'{"custom_id":"part-1"}\n' if part == 1 else REQUEST_BYTES
    binding = {
        "batch_endpoint": "/v1/responses",
        "completion_window": "24h",
        "part": part,
        "prompt_version": "speaker-review-v1",
        "request_sha256": sha256(request).hexdigest(),
        "run_id": RUN_ID,
        "schema_version": 1,
        "stage": "primary",
    }
    value: dict[str, object] = {"binding": binding, "status": "intent"}
    if completed:
        value = {
            "batch_id": f"batch-{part}",
            "binding": binding,
            "input_file_id": f"file-{part}",
            "status": "validating",
        }
    return processor._canonical(value)


def _contents(
    *,
    submitted: bool = False,
    part2_phase: str = "none",
) -> dict[str, bytes]:
    state = _state(submitted=submitted)
    value = {
        "candidates.jsonl": b'{"candidate":"one"}\n',
        "source-manifest.json": b'{"source":"bound"}\n',
        "run-state.json": processor._canonical(state),
        "primary-part-0001-requests.jsonl": b'{"custom_id":"part-1"}\n',
        "primary-part-0002-requests.jsonl": REQUEST_BYTES,
        ".primary-part-0001-submission-intent.json": _journal(1, completed=False),
        ".primary-part-0001-submission-completed.json": _journal(1, completed=True),
        "primary-part-0001-output.jsonl": b'{"response":"complete"}\n',
    }
    if part2_phase in {"intent", "completed"}:
        value[".primary-part-0002-submission-intent.json"] = _journal(2, completed=False)
    if part2_phase == "completed":
        value[".primary-part-0002-submission-completed.json"] = _journal(2, completed=True)
    return value


def _preparation() -> dict[str, object]:
    return {
        "estimated": 250_000,
        "receipt": {
            "artifact_file_count": 5,
            "artifact_set_sha256": "4" * 64,
        },
        "receipt_sha": "5" * 64,
        "result": {
            "candidate_count": 2,
            "estimated_primary_cost_usd": 0.25,
            "primary_part_count": 2,
            "run_id": RUN_ID,
        },
    }


def _worker_result(status: str) -> dict[str, object]:
    return {
        "estimated_primary_cost_microusd": 250_000,
        "operation": contract.OPERATION,
        "primary_completed_part_count": 1,
        "primary_part_count": 2,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "season_number": contract.SEASON_NUMBER,
        "status": status,
        "submitted_part_count": 0 if status == "reconciliation_required" else 1,
    }


def _install_process_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inventories: list[dict[str, bytes]],
) -> Path:
    run = tmp_path / "review-runs" / RUN_ID
    run.mkdir(parents=True)
    receipts = tmp_path / "next-primary-receipts"
    receipts.mkdir()
    receipts.chmod(0o700)
    sequence = iter(inventories)
    monkeypatch.setattr(processor, "NEXT_RECEIPTS_ROOT", receipts)
    monkeypatch.setattr(processor, "ROOT_UID", getattr(os, "getuid", lambda: 0)())
    monkeypatch.setattr(processor, "ROOT_GID", getattr(os, "getgid", lambda: 0)())
    monkeypatch.setattr(processor, "_validate_authorization", lambda _: "1" * 64)
    monkeypatch.setattr(
        processor,
        "_validate_preparation",
        lambda _: (_preparation(), "5" * 64),
    )
    monkeypatch.setattr(processor, "_run_directory", lambda _: run)
    monkeypatch.setattr(
        processor,
        "_inventory",
        lambda *_: (
            (current := next(sequence)),
            json.loads(current["run-state.json"]),
        ),
    )
    monkeypatch.setattr(
        processor,
        "_validate_prior_evidence",
        lambda *_: (
            "2" * 64,
            "3" * 64,
            sha256(REQUEST_BYTES).hexdigest(),
            "6" * 64,
            "7" * 64,
            sha256(_contents()["run-state.json"]).hexdigest(),
        ),
    )
    monkeypatch.setattr(
        processor,
        "_active_binding",
        lambda: ("8" * 40, "ghcr.io/captainvc/cinegraph@sha256:" + "9" * 64, "a" * 64),
    )
    return receipts


def test_process_submits_one_part_and_replay_never_runs_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _contents()
    after = _contents(submitted=True, part2_phase="completed")
    receipts = _install_process_fixture(tmp_path, monkeypatch, [before, after])
    calls: list[dict[str, object]] = []

    def run_worker(request: dict[str, object], _: Path) -> dict[str, object]:
        calls.append(request)
        assert request["_expected_request_sha256"] == sha256(REQUEST_BYTES).hexdigest()
        return _worker_result("submitted")

    monkeypatch.setattr(processor, "_run_worker", run_worker)
    result = processor.process_request(_request())

    assert result == _worker_result("submitted")
    assert len(calls) == 1
    intent_path = receipts / f"{RUN_ID}.intent.json"
    receipt_path = receipts / f"{RUN_ID}.json"
    assert intent_path.exists() and receipt_path.exists()
    stored = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert stored["request_sha256"] == sha256(REQUEST_BYTES).hexdigest()
    assert "batch-2" not in receipt_path.read_text(encoding="utf-8")

    monkeypatch.setattr(
        processor,
        "_inventory",
        lambda *_: (after, json.loads(after["run-state.json"])),
    )
    monkeypatch.setattr(processor, "_run_worker", lambda *_: pytest.fail("worker replay"))
    replay = processor.process_request(_request())

    assert replay == _worker_result("already_submitted")


def test_root_intent_plus_ambiguous_workflow_intent_stays_reconciliation_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _contents()
    ambiguous = _contents(part2_phase="intent")
    receipts = _install_process_fixture(tmp_path, monkeypatch, [before, ambiguous])
    monkeypatch.setattr(
        processor,
        "_run_worker",
        lambda *_: _worker_result("reconciliation_required"),
    )

    first = processor.process_request(_request())
    assert first == _worker_result("reconciliation_required")
    assert (receipts / f"{RUN_ID}.intent.json").exists()
    assert not (receipts / f"{RUN_ID}.json").exists()

    monkeypatch.setattr(
        processor,
        "_inventory",
        lambda *_: (ambiguous, json.loads(ambiguous["run-state.json"])),
    )
    monkeypatch.setattr(processor, "_run_worker", lambda *_: pytest.fail("unsafe retry"))
    second = processor.process_request(_request())
    assert second == _worker_result("reconciliation_required")


def test_completed_journal_repairs_state_through_idempotent_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _contents()
    ambiguous = _contents(part2_phase="intent")
    completed_before_state = _contents(part2_phase="completed")
    after = _contents(submitted=True, part2_phase="completed")
    _install_process_fixture(tmp_path, monkeypatch, [before, ambiguous])
    monkeypatch.setattr(
        processor,
        "_run_worker",
        lambda *_: _worker_result("reconciliation_required"),
    )
    processor.process_request(_request())

    sequence = iter([completed_before_state, after])
    monkeypatch.setattr(
        processor,
        "_inventory",
        lambda *_: (
            (current := next(sequence)),
            json.loads(current["run-state.json"]),
        ),
    )
    calls = 0

    def repair(*_: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return _worker_result("already_submitted")

    monkeypatch.setattr(processor, "_run_worker", repair)
    result = processor.process_request(_request())

    assert calls == 1
    assert result == _worker_result("already_submitted")


def test_orphan_final_receipt_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    after = _contents(submitted=True, part2_phase="completed")
    receipts = _install_process_fixture(tmp_path, monkeypatch, [after])
    (receipts / f"{RUN_ID}.json").write_bytes(processor._canonical({"status": "submitted"}))

    with pytest.raises(processor.NextPrimaryProcessingError, match="orphan"):
        processor.process_request(_request())


def _container_payload(run_root: Path) -> dict[str, object]:
    return {
        "Name": "/cinegraph-speaker-review-submit-next-primary",
        "Config": {
            "Cmd": ["python", "scripts/submit_next_private_speaker_review_workspace.py"],
            "Env": ["CINEGRAPH_ENVIRONMENT=development"],
            "Image": "ghcr.io/captainvc/cinegraph@sha256:" + "9" * 64,
            "Labels": {
                "com.docker.compose.oneoff": "True",
                "com.docker.compose.project": "cinegraph-dev",
                "com.docker.compose.project.config_files": str(processor.COMPOSE_PATH),
                "com.docker.compose.project.working_dir": str(processor.RELEASE_ROOT),
                "com.docker.compose.service": "corpus-speaker-review-submit-next-primary",
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
                "Source": run_root.as_posix(),
            },
            {"Destination": "/run/secrets/openai_api_key", "RW": False, "Source": "/tmp/secret"},
            {"Destination": "/tmp", "RW": True, "Source": ""},
        ],
        "NetworkSettings": {"Networks": {"cinegraph-dev_egress": {}}},
    }


def test_cleanup_identity_rejects_extra_mount_or_secret_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _container_payload(tmp_path)
    monkeypatch.setattr(
        processor.submission,
        "_release_image_reference",
        lambda: "ghcr.io/captainvc/cinegraph@sha256:" + "9" * 64,
    )

    def inspect(*_: object, **__: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess([], 0, json.dumps(payload).encode(), b"")

    monkeypatch.setattr(processor.subprocess, "run", inspect)
    assert processor._container_identity_is_exact(tmp_path)

    payload["Mounts"].append(
        {"Destination": "/host", "RW": True, "Source": "/etc"}  # type: ignore[union-attr]
    )
    assert not processor._container_identity_is_exact(tmp_path)
    payload["Mounts"].pop()  # type: ignore[union-attr]
    payload["Config"]["Env"].append("OPENAI_API_KEY=secret")  # type: ignore[index,union-attr]
    assert not processor._container_identity_is_exact(tmp_path)
    payload["Config"]["Env"].pop()  # type: ignore[index,union-attr]
    payload["Config"]["Labels"]["com.docker.compose.project"] = "cinegraph-other"  # type: ignore[index]
    assert not processor._container_identity_is_exact(tmp_path)


@pytest.mark.parametrize(
    ("section", "field", "invalid"),
    [
        ("Config", "Cmd", ["python", "scripts/other.py"]),
        ("Config", "User", "0:0"),
        ("Config", "WorkingDir", "/"),
        ("HostConfig", "ReadonlyRootfs", False),
        ("HostConfig", "Privileged", True),
        ("HostConfig", "CapDrop", []),
        ("HostConfig", "SecurityOpt", []),
        ("HostConfig", "PidsLimit", 0),
        ("NetworkSettings", "Networks", {"cinegraph-dev_backend": {}}),
    ],
)
def test_cleanup_identity_rejects_wrong_runtime_security_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    section: str,
    field: str,
    invalid: object,
) -> None:
    payload = _container_payload(tmp_path)
    payload[section][field] = invalid  # type: ignore[index]
    monkeypatch.setattr(
        processor.submission,
        "_release_image_reference",
        lambda: "ghcr.io/captainvc/cinegraph@sha256:" + "9" * 64,
    )
    monkeypatch.setattr(
        processor.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, json.dumps(payload).encode(), b""
        ),
    )

    assert not processor._container_identity_is_exact(tmp_path)


def test_active_binding_requires_exact_oci_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = ("8" * 40, "ghcr.io/captainvc/cinegraph@sha256:" + "9" * 64, "a" * 64)
    labels = {
        "org.opencontainers.image.revision": binding[0],
        "org.opencontainers.image.source": "https://github.com/CaptainVC/Cinegraph",
        "org.opencontainers.image.version": f"sha-{binding[0]}",
    }
    monkeypatch.setattr(processor.submission, "_active_runtime_binding", lambda: binding)
    monkeypatch.setattr(
        processor.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, json.dumps(labels).encode(), b""
        ),
    )

    assert processor._active_binding() == binding
    labels["org.opencontainers.image.revision"] = "0" * 40
    with pytest.raises(processor.NextPrimaryProcessingError, match="active runtime"):
        processor._active_binding()


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
    assert captured.err == "error=speaker_review_next_primary_rejected\n"
