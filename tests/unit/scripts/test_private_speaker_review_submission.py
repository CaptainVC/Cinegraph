from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from scripts import private_speaker_review_submission_client as client
from scripts import private_speaker_review_submission_contract as contract
from scripts import submit_private_speaker_review_workspace as worker

from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review.workflow import SpeakerReviewRunState

RUN_ID = "speaker-review-0123456789abcdef"
AUTHORIZATION_ID = "123e4567-e89b-42d3-a456-426614174000"
DIGEST = "a" * 64


def _request(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "archive_sha256": DIGEST,
        "authorization_id": AUTHORIZATION_ID,
        "maximum_authorized_cost_microusd": 5_000_000,
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "schema_version": contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
    }
    value.update(changes)
    return value


def _state(status: SpeakerReviewRunStatus) -> SpeakerReviewRunState:
    return SpeakerReviewRunState(
        schema_version=5,
        run_id=RUN_ID,
        status=status,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        candidate_count=2,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        prompt_version="speaker-review-v1",
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=0.25,
        actual_primary_cost_usd=0.0,
        actual_adjudication_cost_usd=0.0,
        primary_part_count=2,
        primary_batch_id=(
            "batch-private" if status is SpeakerReviewRunStatus.PRIMARY_SUBMITTED else None
        ),
        primary_input_file_id=(
            "file-private" if status is SpeakerReviewRunStatus.PRIMARY_SUBMITTED else None
        ),
        primary_batch_ids=("batch-private",)
        if status is SpeakerReviewRunStatus.PRIMARY_SUBMITTED
        else (),
        primary_input_file_ids=("file-private",)
        if status is SpeakerReviewRunStatus.PRIMARY_SUBMITTED
        else (),
    )


def _environment() -> dict[str, str]:
    return {
        contract.ENV_ARCHIVE_SHA256: DIGEST,
        contract.ENV_AUTHORIZATION_ID: AUTHORIZATION_ID,
        contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: "5000000",
        contract.ENV_RUN_ID: RUN_ID,
    }


def test_submission_request_is_exact_and_rejects_private_fields() -> None:
    raw = contract.canonical_json(_request())
    assert contract.parse_request(raw) == _request()
    with pytest.raises(ValueError):
        contract.parse_request(raw.replace(b"\n", b"\r\n"))
    with pytest.raises(ValueError):
        contract.validate_request({**_request(), "provider_batch_id": "secret"})
    with pytest.raises(ValueError):
        contract.validate_request({**_request(), "authorization_id": AUTHORIZATION_ID.upper()})
    with pytest.raises(ValueError):
        contract.validate_request({**_request(), "maximum_authorized_cost_microusd": 5_000_001})
    for invalid in (True, 1.0, 0, -1):
        with pytest.raises(ValueError):
            contract.validate_request({**_request(), "maximum_authorized_cost_microusd": invalid})


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{}\n",
        b"{" + b'"operation":"submit_primary"' + b"}\ntrailing",
        b'{"archive_sha256":"' + b"a" * 64 + b'","archive_sha256":"' + b"a" * 64 + b'"}\n',
        b"{" + b"x" * (contract.REQUEST_MAX_BYTES + 1) + b"}\n",
    ],
)
def test_submission_request_rejects_malformed_or_oversized_wire(raw: bytes) -> None:
    with pytest.raises(ValueError):
        contract.parse_request(raw)


def test_aggregate_allows_only_safe_submission_states() -> None:
    aggregate = {
        "estimated_primary_cost_microusd": 250_000,
        "operation": contract.OPERATION,
        "primary_part_count": 2,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "season_number": 2,
        "status": "submitted",
        "submitted_part_count": 1,
    }
    assert contract.validate_aggregate(aggregate)["status"] == "submitted"
    with pytest.raises(ValueError):
        contract.validate_aggregate({**aggregate, "provider_batch_id": "secret"})
    with pytest.raises(ValueError):
        contract.validate_aggregate({**aggregate, "submitted_part_count": 2})
    for key, invalid in (
        ("estimated_primary_cost_microusd", True),
        ("estimated_primary_cost_microusd", 1.0),
        ("primary_part_count", True),
        ("submitted_part_count", 1.0),
    ):
        with pytest.raises(ValueError):
            contract.validate_aggregate({**aggregate, key: invalid})
    reconciliation = {**aggregate, "status": "reconciliation_required", "submitted_part_count": 0}
    assert contract.validate_aggregate(reconciliation)["status"] == "reconciliation_required"


def test_client_sends_digest_only_submission_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = tmp_path / "identity"
    identity.write_bytes(b"fixture")
    identity.chmod(0o600)
    host = "dev.example.invalid"
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("fixture\n", encoding="utf-8")
    response = {
        "estimated_primary_cost_microusd": 250_000,
        "operation": contract.OPERATION,
        "primary_part_count": 2,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "season_number": 2,
        "status": "submitted",
        "submitted_part_count": 1,
    }
    observed: dict[str, bytes] = {}

    def run(arguments: list[str], wire: Path) -> subprocess.CompletedProcess[bytes]:
        observed["wire"] = wire.read_bytes()
        return subprocess.CompletedProcess(arguments, 0, contract.canonical_json(response), b"")

    monkeypatch.setattr(client.shutil, "which", lambda _: "ssh")
    monkeypatch.setattr(client, "_validate_known_hosts", lambda *_: None)
    monkeypatch.setattr(client, "_run_ssh", run)
    result = client.submit_primary(
        archive_sha256=DIGEST,
        run_id=RUN_ID,
        authorization_id=AUTHORIZATION_ID,
        maximum_authorized_cost_microusd=5_000_000,
        identity=identity,
        known_hosts=known_hosts,
        host=host,
    )
    assert result == response
    assert b"secret" not in observed["wire"]
    assert observed["wire"] == contract.canonical_json(_request())


def test_worker_replay_does_not_read_secret_or_call_openai(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state(SpeakerReviewRunStatus.PRIMARY_SUBMITTED)
    monkeypatch.setattr(worker, "load_validated_run_state", lambda *_: (tmp_path / RUN_ID, state))
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda *_: pytest.fail("secret read"))
    monkeypatch.setattr(worker, "_workflow", lambda *_: pytest.fail("OpenAI created"))
    result = worker.submit_primary(environment=_environment(), review_root=tmp_path)
    assert result["status"] == "already_submitted"
    assert result["submitted_part_count"] == 1


def test_worker_does_not_load_settings_or_accept_model_environment_overrides() -> None:
    source = Path(worker.__file__).read_text(encoding="utf-8")
    assert "OpenAISettings" not in source
    assert "SecretStr" not in source
    assert "speaker_review_model" in source


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership policy")
def test_secret_rejects_group_or_other_permissions(tmp_path: Path) -> None:
    secret = tmp_path / "openai_api_key"
    secret.write_text("sk-test", encoding="utf-8")
    secret.chmod(0o640)
    with pytest.raises(worker.SubmissionWorkerError):
        worker.read_stable_openai_secret(secret)


def test_worker_submits_only_primary_part_and_caps_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _state(SpeakerReviewRunStatus.PREPARED)
    submitted = _state(SpeakerReviewRunStatus.PRIMARY_SUBMITTED)
    calls: list[Path] = []

    monkeypatch.setattr(
        worker, "load_validated_run_state", lambda *_: (tmp_path / RUN_ID, prepared)
    )
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda *_: "sk-test")

    class Graph:
        def submit(self, path: Path) -> tuple[Path, SpeakerReviewRunState]:
            calls.append(path)
            return path, submitted

    monkeypatch.setattr(worker, "_workflow", lambda _: Graph())
    result = worker.submit_primary(environment=_environment(), review_root=tmp_path)
    assert result["status"] == "submitted"
    assert result["estimated_primary_cost_microusd"] == 250_000
    assert calls == [tmp_path / RUN_ID]


def test_worker_real_loader_uses_review_workspace_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review_root = tmp_path / "review-workspace" / "review-runs"
    run_directory = review_root / RUN_ID
    run_directory.mkdir(parents=True)
    prepared = _state(SpeakerReviewRunStatus.PREPARED)
    (run_directory / "run-state.json").write_text(
        json.dumps(prepared.to_dict(), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda *_: "sk-test")

    class Graph:
        def submit(self, path: Path) -> tuple[Path, SpeakerReviewRunState]:
            assert path == run_directory
            return path, _state(SpeakerReviewRunStatus.PRIMARY_SUBMITTED)

    monkeypatch.setattr(worker, "_workflow", lambda _: Graph())
    result = worker.submit_primary(environment=_environment(), review_root=review_root)
    assert result["status"] == "submitted"


def test_worker_maps_exact_reconciliation_error_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _state(SpeakerReviewRunStatus.PREPARED)
    monkeypatch.setattr(
        worker, "load_validated_run_state", lambda *_: (tmp_path / RUN_ID, prepared)
    )
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda *_: "sk-test")

    class Graph:
        def submit(self, path: Path) -> tuple[Path, SpeakerReviewRunState]:
            del path
            raise RuntimeError(
                "A Batch submission is awaiting operator reconciliation; refusing to submit again."
            )

    monkeypatch.setattr(worker, "_workflow", lambda _: Graph())
    result = worker.submit_primary(environment=_environment(), review_root=tmp_path)
    assert result["status"] == "reconciliation_required"
    assert result["submitted_part_count"] == 0


def test_client_rejects_noncanonical_or_error_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = tmp_path / "identity"
    identity.write_bytes(b"fixture")
    identity.chmod(0o600)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("fixture\n", encoding="utf-8")
    monkeypatch.setattr(client.shutil, "which", lambda _: "ssh")
    monkeypatch.setattr(client, "_validate_known_hosts", lambda *_: None)
    for stdout, stderr, returncode in (
        (b"{}\n", b"", 0),
        (b"", b"provider path leaked\n", 0),
        (b"", b"", 1),
    ):
        monkeypatch.setattr(
            client,
            "_run_ssh",
            lambda arguments,
            wire,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode: subprocess.CompletedProcess(
                arguments, returncode, stdout, stderr
            ),
        )
        with pytest.raises(client.SpeakerReviewSubmissionClientError):
            client.submit_primary(
                archive_sha256=DIGEST,
                run_id=RUN_ID,
                authorization_id=AUTHORIZATION_ID,
                maximum_authorized_cost_microusd=5_000_000,
                identity=identity,
                known_hosts=known_hosts,
                host="dev.example.invalid",
            )
