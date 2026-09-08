from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import private_speaker_review_observation_client as client
from scripts import private_speaker_review_observation_contract as contract

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


def _response(status: str = "waiting") -> dict[str, object]:
    return {
        "estimated_primary_cost_microusd": 250_000,
        "operation": contract.OPERATION,
        "primary_completed_part_count": 0,
        "primary_part_count": 2,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "run_status": "primary_submitted",
        "season_number": contract.SEASON_NUMBER,
        "status": status,
    }


def _inputs(tmp_path: Path) -> tuple[Path, Path]:
    identity = tmp_path / "identity"
    identity.write_bytes(b"fixture")
    identity.chmod(0o600)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("fixture\n", encoding="utf-8")
    return identity, known_hosts


def _observe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run: object,
) -> dict[str, object]:
    identity, known_hosts = _inputs(tmp_path)
    monkeypatch.setattr(client.shutil, "which", lambda _: "ssh")
    monkeypatch.setattr(client, "_validate_known_hosts", lambda *_: None)
    monkeypatch.setattr(client, "_run_ssh", run)
    monkeypatch.setattr(
        client,
        "_host_contract",
        lambda: SimpleNamespace(
            REVIEW_USER="cinegraph-review",
            REVIEW_OBSERVATION_COMMAND=contract.COMMAND,
            REVIEW_OBSERVATION_TIMEOUT_SECONDS=1800,
            REVIEW_OBSERVATION_KILL_AFTER_SECONDS=10,
            CLIENT_TIMEOUT_MARGIN_SECONDS=30,
        ),
    )
    return client.observe_primary(
        archive_sha256=DIGEST,
        run_id=RUN_ID,
        authorization_id=AUTHORIZATION_ID,
        maximum_authorized_cost_microusd=5_000_000,
        identity=identity,
        known_hosts=known_hosts,
        host="dev.example.invalid",
    )


def test_client_sends_only_the_canonical_observation_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    assert contract.COMMAND in captured["arguments"]
    assert "StrictHostKeyChecking=yes" in captured["arguments"]
    assert "ClearAllForwardings=yes" in captured["arguments"]
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
def test_client_rejects_errors_and_noncanonical_or_oversized_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: bytes,
    stderr: bytes,
    returncode: int,
) -> None:
    def run(arguments: list[str], _: Path) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(arguments, returncode, stdout, stderr)

    with pytest.raises(client.SpeakerReviewObservationClientError):
        _observe(tmp_path, monkeypatch, run)


def test_client_rejects_an_unsafe_identity_before_starting_ssh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = tmp_path / "identity"
    target = tmp_path / "target"
    target.write_bytes(b"fixture")
    try:
        identity.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable")
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("fixture\n", encoding="utf-8")
    monkeypatch.setattr(client, "_validate_known_hosts", lambda *_: None)
    monkeypatch.setattr(client.shutil, "which", lambda _: pytest.fail("ssh lookup"))

    with pytest.raises(client.SpeakerReviewObservationClientError, match="safe file"):
        client.observe_primary(
            archive_sha256=DIGEST,
            run_id=RUN_ID,
            authorization_id=AUTHORIZATION_ID,
            maximum_authorized_cost_microusd=5_000_000,
            identity=identity,
            known_hosts=known_hosts,
            host="dev.example.invalid",
        )


def test_cli_returns_only_a_generic_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        client,
        "observe_primary",
        lambda **_: (_ for _ in ()).throw(
            client.SpeakerReviewObservationClientError(
                "sk-private /private/path provider-batch-private"
            )
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
        '{"error":"speaker_review_observation_rejected","status":"error"}\n'
    )
