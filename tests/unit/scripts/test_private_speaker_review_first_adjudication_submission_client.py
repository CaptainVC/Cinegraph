from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest
from scripts import private_speaker_review_first_adjudication_host_contract as host
from scripts import private_speaker_review_first_adjudication_submission_client as client
from scripts import private_speaker_review_first_adjudication_submission_contract as contract
from scripts.private_speaker_review_primary_result_processing_host_contract import (
    SUDOERS_CONTENT as phase68_sudoers,
)

RUN_ID = "speaker-review-0123456789abcdef"
AUTHORIZATION_ID = "123e4567-e89b-42d3-a456-426614174000"
DIGEST = "a" * 64


@pytest.mark.parametrize(
    "change",
    [
        {"run_id": "speaker-review-fedcba9876543210"},
        {
            "actual_primary_cost_microusd": 4_000_000,
            "estimated_adjudication_cost_microusd": 2_000_000,
        },
    ],
)
def test_client_rejects_wrong_run_or_over_budget_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: dict[str, object]
) -> None:
    response = {**aggregate(), **change}

    def runner(arguments: list[str], _: Path) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(arguments, 0, contract.canonical_json(response), b"")

    with pytest.raises(client.FirstAdjudicationSubmissionClientError):
        submit(tmp_path, monkeypatch, runner)


def test_client_rejects_host_command_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host, "REVIEW_FIRST_ADJUDICATION_COMMAND", "unexpected-command")
    with pytest.raises(client.FirstAdjudicationSubmissionClientError):
        client.ssh_arguments(
            ssh="ssh",
            identity=Path("/identity"),
            known_hosts=Path("/known_hosts"),
            host="dev.example.invalid",
        )


def request() -> dict[str, object]:
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


def aggregate() -> dict[str, object]:
    return {
        "actual_primary_cost_microusd": 250_000,
        "adjudication_part_count": 2,
        "estimated_adjudication_cost_microusd": 500_000,
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "run_status": "adjudication_submitted",
        "season_number": contract.SEASON_NUMBER,
        "status": "submitted",
        "submitted_part_count": 1,
    }


def inputs(tmp_path: Path) -> tuple[Path, Path]:
    identity = tmp_path / "identity"
    identity.write_bytes(b"fixture")
    identity.chmod(0o600)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("fixture\n", encoding="utf-8")
    return identity, known_hosts


def submit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: object) -> dict[str, object]:
    identity, known_hosts = inputs(tmp_path)
    monkeypatch.setattr(client.base, "validate_host", lambda value: value)
    monkeypatch.setattr(client.base, "_regular", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(client.base, "_validate_known_hosts", lambda *_args: None)
    monkeypatch.setattr(client.shutil, "which", lambda _: "ssh")
    monkeypatch.setattr(client, "_run_ssh", runner)
    return client.submit_first_adjudication(
        archive_sha256=DIGEST,
        run_id=RUN_ID,
        authorization_id=AUTHORIZATION_ID,
        maximum_authorized_cost_microusd=5_000_000,
        identity=identity,
        known_hosts=known_hosts,
        host="dev.example.invalid",
    )


def test_host_contract_chains_phase68_and_exact_policy() -> None:
    assert host.SUDOERS_CONTENT.startswith(phase68_sudoers)
    assert host.SUDOERS_CONTENT.count(host.REVIEW_FIRST_ADJUDICATION_HELPER_PATH.as_posix()) == 1
    assert host.REVIEW_FIRST_ADJUDICATION_COMMAND == contract.COMMAND
    assert host.REVIEW_FIRST_ADJUDICATION_COMPOSE_PROJECT == "cinegraph-dev"
    assert host.REVIEW_FIRST_ADJUDICATION_NETWORK == "cinegraph-dev_egress"
    assert host.REVIEW_FIRST_ADJUDICATION_CONTAINER_WORKDIR == "/app"
    assert host.REVIEW_FIRST_ADJUDICATION_CONTAINER_COMMAND == (
        "python",
        "scripts/submit_first_private_speaker_review_adjudication_workspace.py",
    )
    assert (
        host.REVIEW_FIRST_ADJUDICATION_WORKER_UID,
        host.REVIEW_FIRST_ADJUDICATION_WORKER_GID,
    ) == (10002, 10002)
    assert host.REVIEW_FIRST_ADJUDICATION_TIMEOUT_SECONDS == 1800
    assert host.REVIEW_FIRST_ADJUDICATION_KILL_AFTER_SECONDS == 10


def test_client_sends_canonical_request_and_pinned_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def runner(arguments: list[str], wire: Path) -> subprocess.CompletedProcess[bytes]:
        captured["arguments"] = arguments
        captured["wire"] = wire.read_bytes()
        return subprocess.CompletedProcess(arguments, 0, contract.canonical_json(aggregate()), b"")

    assert submit(tmp_path, monkeypatch, runner) == aggregate()
    assert captured["wire"] == contract.canonical_json(request())
    arguments = captured["arguments"]
    assert isinstance(arguments, list)
    assert arguments[-1] == contract.COMMAND
    assert "StrictHostKeyChecking=yes" in arguments
    assert "ClearAllForwardings=yes" in arguments
    assert "ProxyCommand=none" in arguments


@pytest.mark.parametrize(
    ("stdout", "stderr", "returncode"),
    [
        (b"{}\n", b"", 0),
        (contract.canonical_json(aggregate()), b"private detail\n", 0),
        (b"", b"", 1),
        (b"{" + b"x" * contract.OUTPUT_MAX_BYTES, b"", 0),
        (b'{"status":"submitted"}\n', b"", 0),
    ],
)
def test_client_rejects_malformed_noncanonical_oversized_or_stderr_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: bytes,
    stderr: bytes,
    returncode: int,
) -> None:
    def runner(arguments: list[str], _: Path) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(arguments, returncode, stdout, stderr)

    with pytest.raises(client.FirstAdjudicationSubmissionClientError):
        submit(tmp_path, monkeypatch, runner)


def test_run_ssh_uses_shell_false_and_bounds_pipes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, object] = {}

    class Process:
        stdout = io.BytesIO(b"o" * (contract.OUTPUT_MAX_BYTES + 1))
        stderr = io.BytesIO(b"e" * (contract.OUTPUT_MAX_BYTES + 1))
        returncode = 0

        def wait(self, timeout: float | None = None) -> int:
            calls["timeout"] = timeout
            return self.returncode

        def poll(self) -> int:
            return self.returncode

    def popen(*args: object, **kwargs: object) -> Process:
        calls["args"] = args
        calls["kwargs"] = kwargs
        return Process()

    monkeypatch.setattr(client.subprocess, "Popen", popen)
    wire = tmp_path / "request.json"
    wire.write_bytes(contract.canonical_json(request()))
    result = client._run_ssh(["ssh", contract.COMMAND], wire)
    assert len(result.stdout) == contract.OUTPUT_MAX_BYTES + 1
    assert len(result.stderr) == contract.OUTPUT_MAX_BYTES + 1
    assert calls["kwargs"]["shell"] is False  # type: ignore[index]
    assert calls["timeout"] == 1800 + 10 + host.CLIENT_TIMEOUT_MARGIN_SECONDS


def test_run_ssh_kills_timed_out_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Process:
        stdout = io.BytesIO(b"")
        stderr = io.BytesIO(b"")
        killed = False

        def wait(self, timeout: float | None = None) -> int:
            if not self.killed:
                raise subprocess.TimeoutExpired("ssh", timeout)
            return 1

        def poll(self) -> None:
            return None if not self.killed else 1

        def kill(self) -> None:
            self.killed = True

    process = Process()
    monkeypatch.setattr(client.subprocess, "Popen", lambda *args, **kwargs: process)
    wire = tmp_path / "request.json"
    wire.write_bytes(b"{}\n")
    with pytest.raises(client.FirstAdjudicationSubmissionClientError):
        client._run_ssh(["ssh", contract.COMMAND], wire)
    assert process.killed


def test_cli_returns_only_generic_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        client,
        "submit_first_adjudication",
        lambda **_: (_ for _ in ()).throw(
            client.FirstAdjudicationSubmissionClientError("/private/provider-id secret")
        ),
    )
    status = client.main(
        [
            "--archive-sha256",
            DIGEST,
            "--run-id",
            RUN_ID,
            "--authorization-id",
            AUTHORIZATION_ID,
            "--maximum-authorized-cost-microusd",
            "5000000",
            "--identity",
            "/private/identity",
            "--known-hosts",
            "/private/known_hosts",
            "--host",
            "dev.example.invalid",
        ]
    )
    captured = capsys.readouterr()
    assert status == 2
    assert captured.out == ""
    assert (
        captured.err == '{"error":"speaker_review_first_adjudication_rejected","status":"error"}\n'
    )
