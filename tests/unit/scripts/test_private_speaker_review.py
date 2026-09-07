from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from scripts import private_speaker_review_client as client
from scripts import private_speaker_review_contract as contract

RUN_ID = "speaker-review-0123456789abcdef"
DIGEST = "a" * 64


def _public_key() -> str:
    blob = b"\x00\x00\x00\x0bssh-ed25519" + b"\x00\x00\x00 " + b"x" * 32
    return "ssh-ed25519 " + base64.b64encode(blob).decode("ascii")


def _request(**changes: object) -> bytes:
    value: dict[str, object] = {
        "archive_sha256": DIGEST,
        "operation": "validate",
        "purpose": contract.REVIEW_PURPOSE,
        "schema_version": contract.REVIEW_PROTOCOL_VERSION,
        "season_number": contract.REVIEW_SEASON_NUMBER,
    }
    value.update(changes)
    return contract.canonical_json(value)


def _aggregate(operation: str = "validate", status: str = "validated") -> dict[str, object]:
    value: dict[str, object] = {
        "file_count": 25,
        "operation": operation,
        "purpose": contract.REVIEW_PURPOSE,
        "season_number": contract.REVIEW_SEASON_NUMBER,
        "status": status,
        "total_bytes": 500,
    }
    if operation != "validate":
        value.update(
            {
                "candidate_count": 4,
                "estimated_primary_cost_usd": 0.25,
                "primary_part_count": 2,
                "run_id": RUN_ID,
            }
        )
    return value


def test_request_is_exact_canonical_newline_json() -> None:
    assert contract.parse_request(_request())["operation"] == "validate"
    for raw in (_request() + b"x", _request().replace(b"\n", b"\r\n")):
        with pytest.raises(ValueError):
            contract.parse_request(raw)
    duplicate = (
        b'{"archive_sha256":"'
        + DIGEST.encode()
        + b'","archive_sha256":"'
        + DIGEST.encode()
        + b'","operation":"validate","purpose":"speaker_review",'
        b'"schema_version":1,"season_number":2}\n'
    )
    with pytest.raises(ValueError):
        contract.parse_request(duplicate)


@pytest.mark.parametrize("operation", ["submit", "advance", "apply", "ingest", "finalize"])
def test_request_rejects_paid_operations(operation: str) -> None:
    with pytest.raises(ValueError):
        contract.parse_request(_request(operation=operation))


def test_status_request_requires_only_the_additional_run_id() -> None:
    status = {
        "archive_sha256": DIGEST,
        "operation": "status",
        "purpose": contract.REVIEW_PURPOSE,
        "run_id": RUN_ID,
        "schema_version": contract.REVIEW_PROTOCOL_VERSION,
        "season_number": contract.REVIEW_SEASON_NUMBER,
    }
    assert contract.parse_request(contract.canonical_json(status)) == status
    with pytest.raises(ValueError):
        contract.parse_request(contract.canonical_json({**status, "extra": 1}))
    with pytest.raises(ValueError):
        contract.parse_request(contract.canonical_json({**status, "run_id": "run"}))


@pytest.mark.parametrize(
    ("operation", "status"),
    [("validate", "prepared"), ("prepare", "validated"), ("status", "validated")],
)
def test_aggregate_status_is_bound_to_operation(operation: str, status: str) -> None:
    with pytest.raises(ValueError):
        contract.validate_aggregate(_aggregate(operation, status), operation=operation)


def test_aggregate_rejects_private_or_provider_fields() -> None:
    response = _aggregate("prepare", "prepared")
    assert (
        contract.parse_aggregate(contract.canonical_json(response), operation="prepare") == response
    )
    with pytest.raises(ValueError):
        contract.validate_aggregate({**response, "provider_payload": "secret"})
    with pytest.raises(ValueError):
        contract.validate_aggregate({**response, "estimated_primary_cost_usd": float("nan")})


@pytest.mark.parametrize("value", [True, "2", 2.0, -1])
def test_validate_aggregate_rejects_non_integer_counts(value: object) -> None:
    response = _aggregate("validate", "validated")
    response["file_count"] = value

    with pytest.raises(ValueError):
        contract.validate_aggregate(response, operation="validate")


@pytest.mark.parametrize("value", [True, 2.0])
def test_validate_aggregate_rejects_non_integer_season_number(value: object) -> None:
    response = _aggregate("validate", "validated")
    response["season_number"] = value

    with pytest.raises(ValueError):
        contract.validate_aggregate(response, operation="validate")


def test_client_sends_only_digest_request_to_static_forced_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = tmp_path / "identity"
    identity.write_bytes(b"private-test-fixture")
    identity.chmod(0o600)
    host = "dev.example.invalid"
    known_hosts = tmp_path / "known hosts"
    known_hosts.write_text(f"{host} {_public_key()}\n", encoding="utf-8")
    source = tmp_path / "bundle.zip"
    source.write_bytes(b"private archive bytes")
    observed: dict[str, object] = {}
    response = contract.canonical_json(_aggregate("prepare", "prepared"))

    def run(arguments: list[str], wire: Path) -> subprocess.CompletedProcess[bytes]:
        observed["arguments"] = arguments
        observed["wire"] = wire.read_bytes()
        return subprocess.CompletedProcess(arguments, 0, response, b"")

    monkeypatch.setattr(client.shutil, "which", lambda _: "ssh")
    monkeypatch.setattr(
        client,
        "_snapshot_bundle",
        lambda _source, destination: (
            len(source.read_bytes()),
            hashlib.sha256(source.read_bytes()).hexdigest(),
        ),
    )
    monkeypatch.setattr(client, "_run_ssh", run)
    result = client.review_bundle(
        bundle=source,
        operation="prepare",
        identity=identity,
        known_hosts=known_hosts,
        host=host,
    )
    assert result == json.loads(response)
    arguments = observed["arguments"]
    assert isinstance(arguments, list)
    assert arguments[-1] == contract.REVIEW_COMMAND
    assert f"cinegraph-corpus@{host}" in arguments
    assert str(source) not in arguments
    wire = observed["wire"]
    assert isinstance(wire, bytes)
    assert source.read_bytes() not in wire
    assert wire == contract.canonical_json(
        {
            "archive_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "operation": "prepare",
            "purpose": contract.REVIEW_PURPOSE,
            "schema_version": contract.REVIEW_PROTOCOL_VERSION,
            "season_number": contract.REVIEW_SEASON_NUMBER,
        }
    )
    for option in (
        "StrictHostKeyChecking=yes",
        "IdentitiesOnly=yes",
        "BatchMode=yes",
        "ClearAllForwardings=yes",
        "ForwardAgent=no",
        "RequestTTY=no",
        "ProxyCommand=none",
        "ProxyJump=none",
        "HostKeyAlgorithms=ssh-ed25519",
    ):
        assert option in arguments


def test_status_client_requires_digest_and_run_id_without_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = tmp_path / "identity"
    identity.write_bytes(b"fixture")
    identity.chmod(0o600)
    known_hosts = tmp_path / "known_hosts"
    host = "dev.example.invalid"
    known_hosts.write_text(f"{host} {_public_key()}\n", encoding="utf-8")
    monkeypatch.setattr(client.shutil, "which", lambda _: "ssh")
    response = contract.canonical_json(_aggregate("status", "prepared"))
    observed: dict[str, bytes] = {}

    def run(arguments: list[str], wire: Path) -> subprocess.CompletedProcess[bytes]:
        observed["wire"] = wire.read_bytes()
        return subprocess.CompletedProcess(arguments, 0, response, b"")

    monkeypatch.setattr(client, "_run_ssh", run)
    result = client.review_bundle(
        bundle=None,
        archive_sha256=DIGEST,
        operation="status",
        run_id=RUN_ID,
        identity=identity,
        known_hosts=known_hosts,
        host=host,
    )
    assert result["status"] == "prepared"
    assert b"run_id" in observed["wire"]
    with pytest.raises(client.SpeakerReviewClientError):
        client.review_bundle(
            bundle=None,
            operation="status",
            identity=identity,
            known_hosts=known_hosts,
            host=host,
            run_id=RUN_ID,
        )
