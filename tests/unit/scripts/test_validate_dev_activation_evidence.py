from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from scripts.validate_dev_activation_evidence import (
    EvidenceValidationError,
    main,
    validate_evidence,
)

EXAMPLE = Path("docs/evidence/dev-activation.example.json")


@pytest.fixture
def evidence() -> dict[str, object]:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_example_is_valid(evidence: dict[str, object]) -> None:
    validate_evidence(evidence)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bootstrap_sha", "A" * 40),
        ("release_sha", "a" * 39),
        ("image_tag", f"sha-{'a' * 39}"),
        ("image_digest", f"sha256:{'A' * 64}"),
        ("image_digest", f"sha256:{'a' * 63}"),
    ],
)
def test_rejects_malformed_immutable_identifiers(
    evidence: dict[str, object], field: str, value: str
) -> None:
    evidence[field] = value

    with pytest.raises(EvidenceValidationError):
        validate_evidence(evidence)


def test_requires_image_tag_to_match_release_sha(evidence: dict[str, object]) -> None:
    evidence["image_tag"] = f"sha-{'f' * 40}"

    with pytest.raises(EvidenceValidationError):
        validate_evidence(evidence)


@pytest.mark.parametrize("run_name", ["quality_run", "publish_run", "deploy_run"])
def test_rejects_wrong_repository_or_non_actions_run_url(
    evidence: dict[str, object], run_name: str
) -> None:
    run = evidence[run_name]
    assert isinstance(run, dict)
    run["url"] = "https://github.com/SomeoneElse/Cinegraph/actions/runs/10000000001"

    with pytest.raises(EvidenceValidationError):
        validate_evidence(evidence)


@pytest.mark.parametrize("run_name", ["quality_run", "publish_run", "deploy_run"])
def test_requires_successful_run_conclusions(evidence: dict[str, object], run_name: str) -> None:
    run = evidence[run_name]
    assert isinstance(run, dict)
    run["conclusion"] = "failure"

    with pytest.raises(EvidenceValidationError):
        validate_evidence(evidence)


def test_requires_distinct_workflow_run_urls(evidence: dict[str, object]) -> None:
    quality_run = evidence["quality_run"]
    publish_run = evidence["publish_run"]
    assert isinstance(quality_run, dict)
    assert isinstance(publish_run, dict)
    publish_run["url"] = quality_run["url"]

    with pytest.raises(EvidenceValidationError):
        validate_evidence(evidence)


def test_requires_exact_dev_schema(evidence: dict[str, object]) -> None:
    evidence["environment"] = "prod"

    with pytest.raises(EvidenceValidationError):
        validate_evidence(evidence)

    extra = copy.deepcopy(evidence)
    extra["environment"] = "dev"
    extra["notes"] = "unexpected"
    with pytest.raises(EvidenceValidationError):
        validate_evidence(extra)


@pytest.mark.parametrize("schema_version", [True, 1.0])
def test_schema_version_is_type_strict(
    evidence: dict[str, object], schema_version: object
) -> None:
    evidence["schema_version"] = schema_version

    with pytest.raises(EvidenceValidationError):
        validate_evidence(evidence)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("private_key", "placeholder"),
        ("known_hosts", "placeholder"),
        ("env_contents", "placeholder"),
        ("corpus_path", "placeholder"),
        ("ip_address", "placeholder"),
        ("password", "placeholder"),
        ("access_token", "placeholder"),
        ("database_url", "placeholder"),
    ],
)
def test_rejects_private_or_host_material_fields(
    evidence: dict[str, object], field: str, value: str
) -> None:
    evidence[field] = value

    with pytest.raises(EvidenceValidationError):
        validate_evidence(evidence)


@pytest.mark.parametrize("timestamp", ["2030-02-29T03:04:05Z", "2030-04-31T03:04:05Z"])
def test_rejects_nonexistent_calendar_dates(
    evidence: dict[str, object], timestamp: str
) -> None:
    evidence["observed_at"] = timestamp

    with pytest.raises(EvidenceValidationError):
        validate_evidence(evidence)


@pytest.mark.parametrize(
    "value",
    [
        "-----BEGIN OPENSSH " + "PRIVATE KEY-----",
        "OPENAI_API_KEY=not-a-real-secret",
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakePublicKeyMaterial",
        "/srv/corpus/private",
        "episode.srt",
        "192.0.2.10",
    ],
)
def test_rejects_private_or_host_material_values(evidence: dict[str, object], value: str) -> None:
    evidence["unexpected"] = value

    with pytest.raises(EvidenceValidationError):
        validate_evidence(evidence)


def test_requires_verified_attestation_and_readiness(evidence: dict[str, object]) -> None:
    attestation = evidence["attestation"]
    assert isinstance(attestation, dict)
    attestation["verified"] = False
    with pytest.raises(EvidenceValidationError):
        validate_evidence(evidence)

    attestation["verified"] = True
    evidence["readiness_verified"] = False
    with pytest.raises(EvidenceValidationError):
        validate_evidence(evidence)


def test_cli_failure_does_not_echo_submitted_values(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    secret_marker = "OPENAI_API_KEY=must-not-appear"
    evidence_file = tmp_path / "evidence.json"
    evidence_file.write_text(json.dumps({"private_key": secret_marker}), encoding="utf-8")

    assert main([str(evidence_file)]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "activation evidence is invalid\n"
    assert secret_marker not in output.err


def test_cli_rejects_duplicate_fields_without_echoing_values(
    evidence: dict[str, object], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret_marker = "OPENAI_API_KEY=must-not-appear"
    evidence_file = tmp_path / "evidence.json"
    fingerprint = evidence["host_key_fingerprint"]
    assert isinstance(fingerprint, str)
    serialized = json.dumps(evidence, separators=(",", ":"))
    canonical_field = f'"host_key_fingerprint":"{fingerprint}"'
    duplicate_fields = (
        f'"host_key_fingerprint":"{secret_marker}",{canonical_field}'
    )
    duplicate_payload = serialized.replace(canonical_field, duplicate_fields, 1)
    assert duplicate_payload != serialized
    evidence_file.write_text(duplicate_payload, encoding="utf-8")

    assert main([str(evidence_file)]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "activation evidence is invalid\n"
    assert secret_marker not in output.err


def test_cli_accepts_example(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(EXAMPLE)]) == 0
    output = capsys.readouterr()
    assert output.out == "activation evidence is valid\n"
    assert output.err == ""
