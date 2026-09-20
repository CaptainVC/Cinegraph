from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from scripts import private_speaker_review_adjudication_result_processing_client as client
from scripts import private_speaker_review_adjudication_result_processing_contract as contract

RUN_ID = "speaker-review-0123456789abcdef"
AUTHORIZATION_ID = "123e4567-e89b-42d3-a456-426614174000"
DIGEST = "a" * 64


def _response() -> dict[str, object]:
    return {
        "accepted_by_consensus": 1,
        "accepted_by_adjudication": 0,
        "actual_adjudication_cost_microusd": 2,
        "actual_primary_cost_microusd": 3,
        "adjudication_completed_part_count": 1,
        "adjudication_part_count": 1,
        "candidate_count": 2,
        "final_review_part_count": 1,
        "maximum_authorized_cost_microusd": 5_000_000,
        "needs_human": 1,
        "operation": contract.OPERATION,
        "primary_completed_part_count": 1,
        "primary_part_count": 1,
        "purpose": contract.PURPOSE,
        "run_status": "final_review_prepared",
        "season_number": contract.SEASON_NUMBER,
        "status": "final_review_prepared",
    }


def _process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run: object,
    *,
    ssh_available: bool = True,
) -> dict[str, object]:
    identity = tmp_path / "identity"
    identity.write_bytes(b"fixture")
    identity.chmod(0o600)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("fixture\n", encoding="utf-8")
    monkeypatch.setattr(client, "validate_host", lambda value: value)
    monkeypatch.setattr(client.base, "_regular", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(client.base, "_validate_known_hosts", lambda *_: None)
    monkeypatch.setattr(client.shutil, "which", lambda _: "ssh" if ssh_available else None)
    monkeypatch.setattr(client, "_run_ssh", run)
    return client.process_adjudication_results(
        archive_sha256=DIGEST,
        run_id=RUN_ID,
        authorization_id=AUTHORIZATION_ID,
        maximum_authorized_cost_microusd=5_000_000,
        identity=identity,
        known_hosts=known_hosts,
        host="dev.example.invalid",
    )


def test_client_sends_canonical_request_over_exact_pinned_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def run(arguments: list[str], wire: Path) -> subprocess.CompletedProcess[bytes]:
        captured["arguments"] = arguments
        captured["wire"] = wire.read_bytes()
        return subprocess.CompletedProcess(arguments, 0, contract.canonical_json(_response()), b"")

    assert _process(tmp_path, monkeypatch, run) == _response()
    assert captured["wire"] == contract.canonical_json(
        {
            "archive_sha256": DIGEST,
            "authorization_id": AUTHORIZATION_ID,
            "maximum_authorized_cost_microusd": 5_000_000,
            "operation": contract.OPERATION,
            "purpose": contract.PURPOSE,
            "run_id": RUN_ID,
            "schema_version": contract.PROTOCOL_VERSION,
            "season_number": contract.SEASON_NUMBER,
        }
    )
    arguments = captured["arguments"]
    assert isinstance(arguments, list)
    assert arguments[-1] == contract.COMMAND
    assert "StrictHostKeyChecking=yes" in arguments
    assert "ClearAllForwardings=yes" in arguments
    assert "UserKnownHostsFile" in " ".join(arguments)


@pytest.mark.parametrize(
    ("stdout", "stderr", "returncode"),
    [
        (b"{}\n", b"", 0),
        (contract.canonical_json(_response()), b"private provider detail\n", 0),
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

    with pytest.raises(client.AdjudicationResultProcessingClientError):
        _process(tmp_path, monkeypatch, run)


def test_client_rejects_missing_ssh_without_remote_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoked = False

    def run(_: list[str], __: Path) -> subprocess.CompletedProcess[bytes]:
        nonlocal invoked
        invoked = True
        return subprocess.CompletedProcess([], 0, b"", b"")

    monkeypatch.setattr(client.shutil, "which", lambda _: None)
    with pytest.raises(client.AdjudicationResultProcessingClientError, match="OpenSSH"):
        _process(tmp_path, monkeypatch, run, ssh_available=False)
    assert not invoked


def test_cli_emits_only_generic_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        client,
        "process_adjudication_results",
        lambda **_: (_ for _ in ()).throw(
            client.AdjudicationResultProcessingClientError("/private/provider-output")
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
        '{"error":"speaker_review_adjudication_result_processing_rejected","status":"error"}\n'
    )


def test_operator_entrypoint_delegates_to_the_pinned_client() -> None:
    entrypoint = Path("scripts/process_private_speaker_review_adjudication_results.py").read_text(
        encoding="utf-8"
    )

    assert "private_speaker_review_adjudication_result_processing_client" in entrypoint
    assert "main" in entrypoint
