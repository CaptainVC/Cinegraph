from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from scripts import private_speaker_review_third_adjudication_observation_client as client
from scripts import private_speaker_review_third_adjudication_observation_contract as contract

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


def _aggregate(*, status: str = "observed") -> dict[str, object]:
    completed = 3 if status in {"observed", "already_observed"} else 2
    run_status = (
        "adjudication_part_completed"
        if completed == 3
        else "failed"
        if status == "failed"
        else "adjudication_submitted"
    )
    return {
        "actual_primary_cost_microusd": 250_000,
        "adjudication_completed_part_count": completed,
        "adjudication_part_count": 3,
        "estimated_adjudication_cost_microusd": 500_000,
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "run_status": run_status,
        "season_number": contract.SEASON_NUMBER,
        "status": status,
    }


def _observe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: object) -> dict[str, object]:
    identity = tmp_path / "identity"
    identity.write_bytes(b"fixture")
    identity.chmod(0o600)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("fixture\n", encoding="utf-8")
    monkeypatch.setattr(client, "validate_host", lambda value: value)
    monkeypatch.setattr(client.base, "_regular", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(client.base, "_validate_known_hosts", lambda *_args: None)
    monkeypatch.setattr(client.shutil, "which", lambda _: "ssh")
    monkeypatch.setattr(client.base, "_run_ssh", runner)
    return client.observe_third_adjudication(
        archive_sha256=DIGEST,
        run_id=RUN_ID,
        authorization_id=AUTHORIZATION_ID,
        maximum_authorized_cost_microusd=5_000_000,
        identity=identity,
        known_hosts=known_hosts,
        host="dev.example.invalid",
    )


def test_client_sends_only_canonical_request_over_pinned_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def runner(arguments: list[str], wire: Path) -> subprocess.CompletedProcess[bytes]:
        captured["arguments"] = arguments
        captured["wire"] = wire.read_bytes()
        return subprocess.CompletedProcess(arguments, 0, contract.canonical_json(_aggregate()), b"")

    assert _observe(tmp_path, monkeypatch, runner) == _aggregate()
    assert captured["wire"] == contract.canonical_json(_request())
    arguments = captured["arguments"]
    assert isinstance(arguments, list)
    assert arguments[-1] == contract.COMMAND
    assert "StrictHostKeyChecking=yes" in arguments
    assert "ClearAllForwardings=yes" in arguments
    assert all(secret not in str(captured) for secret in ("provider_batch_id", "subtitle", "OPENAI_API_KEY"))


@pytest.mark.parametrize("status", ["waiting", "already_observed", "failed", "reconciliation_required"])
def test_client_accepts_each_safe_aggregate_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    result = _aggregate(status=status)
    monkeypatch.setattr(client.base, "_run_ssh", lambda arguments, _: subprocess.CompletedProcess(arguments, 0, contract.canonical_json(result), b""))
    assert _observe(tmp_path, monkeypatch, client.base._run_ssh)["status"] == status


@pytest.mark.parametrize(
    ("stdout", "stderr", "returncode"),
    [(b"{}\n", b"", 0), (contract.canonical_json(_aggregate()), b"private detail\n", 0), (b"", b"", 1), (b"x" * (contract.OUTPUT_MAX_BYTES + 1), b"", 0)],
)
def test_client_rejects_malformed_private_or_oversized_remote_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: bytes, stderr: bytes, returncode: int) -> None:
    def runner(arguments: list[str], _: Path) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(arguments, returncode, stdout, stderr)

    with pytest.raises(client.ThirdAdjudicationObservationClientError):
        _observe(tmp_path, monkeypatch, runner)


def test_contract_rejects_wrong_status_count_id_or_duplicate_wire_keys() -> None:
    request = _request()
    assert contract.parse_request(contract.canonical_json(request)) == request
    with pytest.raises(ValueError):
        contract.validate_request({**request, "operation": "submit_third_adjudication"})
    for change in (
        {"adjudication_completed_part_count": 2, "status": "observed"},
        {"run_status": "adjudication_submitted", "status": "observed"},
        {"adjudication_part_count": 1},
        {"run_id": "speaker-review-not-a-run"},
    ):
        with pytest.raises(ValueError):
            contract.validate_aggregate({**_aggregate(), **change})
    duplicate = b'{"archive_sha256":"' + DIGEST.encode() + b'","archive_sha256":"' + DIGEST.encode() + b'","authorization_id":"' + AUTHORIZATION_ID.encode() + b'","maximum_authorized_cost_microusd":5000000,"operation":"observe_third_adjudication","purpose":"speaker_review","run_id":"' + RUN_ID.encode() + b'","schema_version":1,"season_number":2}\n'
    with pytest.raises(ValueError):
        contract.parse_request(duplicate)


def test_cli_never_leaks_private_error_details(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(client, "observe_third_adjudication", lambda **_: (_ for _ in ()).throw(client.ThirdAdjudicationObservationClientError("sk-private /secret")))
    assert client.main(["--archive-sha256", DIGEST, "--run-id", RUN_ID, "--authorization-id", AUTHORIZATION_ID, "--maximum-authorized-cost-microusd", "5000000", "--identity", "identity", "--known-hosts", "known_hosts", "--host", "dev.example.invalid"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == '{"error":"speaker_review_third_adjudication_observation_rejected","status":"error"}\n'
