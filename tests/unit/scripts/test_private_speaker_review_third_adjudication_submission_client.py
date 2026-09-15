from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from scripts import private_speaker_review_third_adjudication_host_contract as host
from scripts import private_speaker_review_third_adjudication_submission_client as client
from scripts import private_speaker_review_third_adjudication_submission_contract as contract

from cinegraph.common.error_messages import SpeakerReviewErrorMessages

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


def _aggregate() -> dict[str, object]:
    return {
        "actual_primary_cost_microusd": 250_000,
        "adjudication_completed_part_count": 2,
        "adjudication_part_count": 3,
        "estimated_adjudication_cost_microusd": 500_000,
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "run_status": "adjudication_submitted",
        "season_number": contract.SEASON_NUMBER,
        "status": "submitted",
        "submitted_part_count": 1,
    }


def _submit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner: object,
) -> dict[str, object]:
    identity = tmp_path / "identity"
    identity.write_bytes(b"fixture")
    identity.chmod(0o600)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("fixture\n", encoding="utf-8")
    monkeypatch.setattr(client.base, "validate_host", lambda value: value)
    monkeypatch.setattr(client.base, "_regular", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(client.base, "_validate_known_hosts", lambda *_args: None)
    monkeypatch.setattr(client.shutil, "which", lambda _: "ssh")
    monkeypatch.setattr(client, "_run_ssh", runner)
    return client.submit_third_adjudication(
        archive_sha256=DIGEST,
        run_id=RUN_ID,
        authorization_id=AUTHORIZATION_ID,
        maximum_authorized_cost_microusd=5_000_000,
        identity=identity,
        known_hosts=known_hosts,
        host="dev.example.invalid",
    )


def test_client_sends_canonical_request_over_pinned_third_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def runner(arguments: list[str], wire: Path) -> subprocess.CompletedProcess[bytes]:
        captured["arguments"] = arguments
        captured["wire"] = wire.read_bytes()
        return subprocess.CompletedProcess(
            arguments, 0, contract.canonical_json(_aggregate()), b""
        )

    assert _submit(tmp_path, monkeypatch, runner) == _aggregate()
    assert captured["wire"] == contract.canonical_json(_request())
    arguments = captured["arguments"]
    assert isinstance(arguments, list)
    assert arguments[-1] == contract.COMMAND
    assert "StrictHostKeyChecking=yes" in arguments
    assert "ClearAllForwardings=yes" in arguments


@pytest.mark.parametrize(
    "change",
    [
        {"run_id": "speaker-review-fedcba9876543210"},
        {"run_status": "adjudication_part_completed"},
        {"adjudication_completed_part_count": 1},
        {
            "actual_primary_cost_microusd": 4_000_000,
            "estimated_adjudication_cost_microusd": 2_000_000,
        },
    ],
)
def test_client_rejects_unbound_non_part_three_or_over_budget_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: dict[str, object],
) -> None:
    response = {**_aggregate(), **change}

    def runner(arguments: list[str], _: Path) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            arguments, 0, contract.canonical_json(response), b""
        )

    with pytest.raises(client.ThirdAdjudicationSubmissionClientError):
        _submit(tmp_path, monkeypatch, runner)


@pytest.mark.parametrize(
    ("stdout", "stderr", "returncode"),
    [
        (b"{}\n", b"", 0),
        (contract.canonical_json(_aggregate()), b"private detail\n", 0),
        (b"", b"", 1),
        (b"{" + b"x" * contract.OUTPUT_MAX_BYTES, b"", 0),
    ],
)
def test_client_rejects_malformed_oversized_or_stderr_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: bytes,
    stderr: bytes,
    returncode: int,
) -> None:
    def runner(arguments: list[str], _: Path) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(arguments, returncode, stdout, stderr)

    with pytest.raises(client.ThirdAdjudicationSubmissionClientError):
        _submit(tmp_path, monkeypatch, runner)


def test_client_rejects_host_command_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host, "REVIEW_THIRD_ADJUDICATION_COMMAND", "unexpected")
    with pytest.raises(client.ThirdAdjudicationSubmissionClientError):
        client.ssh_arguments(
            ssh="ssh",
            identity=Path("/identity"),
            known_hosts=Path("/known-hosts"),
            host="dev.example.invalid",
        )


def test_contract_rejects_non_part_three_aggregate() -> None:
    with pytest.raises(ValueError):
        contract.validate_aggregate(
            {**_aggregate(), "adjudication_completed_part_count": 1}
        )
    with pytest.raises(ValueError):
        contract.validate_aggregate({**_aggregate(), "adjudication_part_count": 2})


def test_contract_recognizes_generic_next_part_reconciliation_error() -> None:
    assert contract.is_reconciliation_error(
        RuntimeError(
            SpeakerReviewErrorMessages.NEXT_ADJUDICATION_SUBMISSION_RECONCILIATION_REQUIRED
        )
    )
