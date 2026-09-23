from __future__ import annotations

import io
from pathlib import Path

import pytest
from scripts import private_speaker_review_final_review_submission_client as client
from scripts import private_speaker_review_final_review_submission_contract as contract

RUN_ID = "speaker-review-0123456789abcdef"
AUTHORIZATION_ID = "123e4567-e89b-42d3-a456-426614174000"
DIGEST = "a" * 64


def _response() -> dict[str, object]:
    return {
        "actual_primary_cost_microusd": 100,
        "actual_adjudication_cost_microusd": 200,
        "estimated_final_review_cost_microusd": 300,
        "final_review_completed_part_count": 0,
        "final_review_part_count": 2,
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "run_status": "final_review_submitted",
        "season_number": contract.SEASON_NUMBER,
        "status": "submitted",
        "submitted_part_count": 1,
    }


def test_ssh_arguments_inherit_strict_pinned_policy(tmp_path: Path) -> None:
    arguments = client.ssh_arguments(
        ssh="ssh",
        identity=(tmp_path / "identity").resolve(),
        known_hosts=(tmp_path / "known_hosts").resolve(),
        host_name="dev.example.invalid",
    )

    assert arguments[-1] == contract.COMMAND
    for option in (
        "BatchMode=yes",
        "StrictHostKeyChecking=yes",
        "ClearAllForwardings=yes",
        "ForwardAgent=no",
        "PasswordAuthentication=no",
        "KbdInteractiveAuthentication=no",
        "RequestTTY=no",
        "ProxyCommand=none",
        "ProxyJump=none",
        "UpdateHostKeys=no",
    ):
        assert option in arguments


def test_pipe_drains_but_retains_only_public_limit() -> None:
    raw = b"x" * (contract.OUTPUT_MAX_BYTES * 3)

    retained = client._pipe(io.BytesIO(raw))

    assert retained == raw[: contract.OUTPUT_MAX_BYTES + 1]


def test_response_must_bind_run_and_authorized_total(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = tmp_path / "identity"
    identity.write_bytes(b"private")
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("fixture\n", encoding="utf-8")
    monkeypatch.setattr(client.base, "_regular", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(client.base, "_validate_known_hosts", lambda *_args: None)
    monkeypatch.setattr(client.shutil, "which", lambda _: "ssh")
    response = _response()
    response["run_id"] = "speaker-review-ffffffffffffffff"

    class Process:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.stdout = io.BytesIO(contract.canonical_json(response))
            self.stderr = io.BytesIO()

        def wait(self, *, timeout: float | None = None) -> int:
            del timeout
            return 0

        def kill(self) -> None:
            return None

    monkeypatch.setattr(client.subprocess, "Popen", Process)

    with pytest.raises(client.FinalReviewSubmissionClientError, match="response invalid"):
        client.submit_final_review(
            archive_sha256=DIGEST,
            run_id=RUN_ID,
            authorization_id=AUTHORIZATION_ID,
            maximum_authorized_cost_microusd=5_000_000,
            identity=identity,
            known_hosts=known_hosts,
            host_name="dev.example.invalid",
        )


def test_cli_emits_only_generic_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        client,
        "submit_final_review",
        lambda **_: (_ for _ in ()).throw(
            client.FinalReviewSubmissionClientError("/private/provider-payload")
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
        '{"error":"speaker_review_final_review_rejected","status":"error"}\n'
    )
