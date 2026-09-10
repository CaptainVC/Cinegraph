from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from scripts import private_speaker_review_next_primary_observation_client as client
from scripts import private_speaker_review_next_primary_observation_contract as contract

RUN_ID = "speaker-review-0123456789abcdef"
AUTHORIZATION_ID = "123e4567-e89b-42d3-a456-426614174000"
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


def _response() -> dict[str, object]:
    return {
        "estimated_primary_cost_microusd": 250_000,
        "operation": contract.OPERATION,
        "primary_completed_part_count": 2,
        "primary_part_count": 2,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "run_status": "primary_part_completed",
        "season_number": contract.SEASON_NUMBER,
        "status": "observed",
    }


def _observe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run: object,
) -> dict[str, object]:
    identity = tmp_path / "identity"
    identity.write_bytes(b"fixture")
    identity.chmod(0o600)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("fixture\n", encoding="utf-8")
    monkeypatch.setattr(client, "validate_host", lambda value: value)
    monkeypatch.setattr(client.base, "_regular", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(client.base, "_validate_known_hosts", lambda *_: None)
    monkeypatch.setattr(client.shutil, "which", lambda _: "ssh")
    monkeypatch.setattr(client.base, "_run_ssh", run)
    return client.observe_next_primary(
        archive_sha256=DIGEST,
        run_id=RUN_ID,
        authorization_id=AUTHORIZATION_ID,
        maximum_authorized_cost_microusd=5_000_000,
        identity=identity,
        known_hosts=known_hosts,
        host="dev.example.invalid",
    )


def test_client_sends_only_canonical_request_over_exact_pinned_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def run(arguments: list[str], wire: Path) -> subprocess.CompletedProcess[bytes]:
        captured["arguments"] = arguments
        captured["wire"] = wire.read_bytes()
        return subprocess.CompletedProcess(
            arguments,
            0,
            contract.canonical_json(_response()),
            b"",
        )

    result = _observe(tmp_path, monkeypatch, run)

    assert result == _response()
    assert captured["wire"] == contract.canonical_json(_request())
    arguments = captured["arguments"]
    assert isinstance(arguments, list)
    assert arguments[-1] == contract.COMMAND
    assert "StrictHostKeyChecking=yes" in arguments
    assert "ClearAllForwardings=yes" in arguments
    assert all(
        private not in str(captured)
        for private in ("provider_batch_id", "subtitle", "OPENAI_API_KEY")
    )


@pytest.mark.parametrize(
    ("stdout", "stderr", "returncode"),
    [
        (b"{}\n", b"", 0),
        (contract.canonical_json(_response()), b"private detail\n", 0),
        (b"", b"", 1),
        (b"x" * (contract.OUTPUT_MAX_BYTES + 1), b"", 0),
    ],
)
def test_client_rejects_invalid_or_private_remote_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: bytes,
    stderr: bytes,
    returncode: int,
) -> None:
    def run(arguments: list[str], _: Path) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(arguments, returncode, stdout, stderr)

    with pytest.raises(client.NextPrimaryObservationClientError):
        _observe(tmp_path, monkeypatch, run)


def test_ssh_arguments_cannot_select_an_arbitrary_command(tmp_path: Path) -> None:
    arguments = client.ssh_arguments(
        ssh="ssh",
        identity=tmp_path / "identity",
        known_hosts=tmp_path / "known_hosts",
        host="dev.example.invalid",
    )
    assert arguments[-1] == contract.COMMAND


def test_cli_returns_only_generic_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        client,
        "observe_next_primary",
        lambda **_: (_ for _ in ()).throw(
            client.NextPrimaryObservationClientError("sk-private /private/path batch-private")
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
            "identity",
            "--known-hosts",
            "known_hosts",
            "--host",
            "dev.example.invalid",
        ]
    )

    captured = capsys.readouterr()
    assert status == 2
    assert captured.out == ""
    assert captured.err == (
        '{"error":"speaker_review_next_primary_observation_rejected","status":"error"}\n'
    )
