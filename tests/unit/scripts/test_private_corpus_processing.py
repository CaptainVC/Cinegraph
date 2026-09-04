from __future__ import annotations

import base64
import hashlib
import io
import json
import subprocess
from pathlib import Path

import pytest
from scripts import private_corpus_processing_contract as contract
from scripts import process_private_corpus_client as client
from scripts import run_private_corpus_processing as processor


def _public_key() -> str:
    blob = b"\x00\x00\x00\x0bssh-ed25519" + b"\x00\x00\x00 " + b"x" * 32
    return "ssh-ed25519 " + base64.b64encode(blob).decode("ascii")


def _request(**changes: object) -> bytes:
    value: dict[str, object] = {
        "archive_sha256": "a" * 64,
        "operation": "validate",
        "purpose": "reviewed_ingestion",
        "schema_version": 1,
        "season_number": 1,
    }
    value.update(changes)
    return contract.canonical_json(value)


def test_request_requires_canonical_newline_and_exact_eof() -> None:
    assert contract.parse_request(_request())["operation"] == "validate"
    with pytest.raises(ValueError):
        contract.parse_request(_request() + b"x")
    with pytest.raises(ValueError):
        contract.parse_request(_request().replace(b"\n", b"\r\n"))
    duplicate = (
        b'{"archive_sha256":"'
        + b"a" * 64
        + b'","archive_sha256":"'
        + b"a" * 64
        + b'","operation":"validate","purpose":"reviewed_ingestion","schema_version":1,"season_number":1}\n'
    )
    with pytest.raises(ValueError):
        contract.parse_request(duplicate)


@pytest.mark.parametrize("operation", ["", "apply", "validate "])
def test_request_rejects_unknown_operation(operation: str) -> None:
    with pytest.raises(ValueError):
        contract.parse_request(_request(operation=operation))


def test_aggregate_rejects_a_status_from_the_other_operation() -> None:
    aggregate = {
        "episode_count": 1,
        "file_count": 2,
        "indexed_segment_count": 0,
        "mode": "validate",
        "purpose": "reviewed_ingestion",
        "season_number": 1,
        "status": "applied",
        "total_bytes": 10,
    }
    with pytest.raises(ValueError):
        contract.validate_aggregate(aggregate, mode="validate", status="applied")


def test_client_uses_quoted_forward_slash_known_hosts_path_without_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = tmp_path / "identity"
    identity.write_bytes(b"synthetic")
    identity.chmod(0o600)
    known_hosts = tmp_path / "known hosts"
    host = "dev.example.invalid"
    known_hosts.write_text(f"{host} {_public_key()}\n", encoding="utf-8")
    captured: dict[str, object] = {}
    response = {
        "episode_count": 1,
        "file_count": 2,
        "indexed_segment_count": 0,
        "mode": "validate",
        "purpose": "reviewed_ingestion",
        "season_number": 1,
        "status": "validated",
        "total_bytes": 10,
    }

    def snapshot(_source: Path, destination: Path) -> tuple[int, str]:
        destination.write_bytes(b"bundle")
        return 6, hashlib.sha256(b"bundle").hexdigest()

    def run(arguments: list[str], wire: Path) -> subprocess.CompletedProcess[bytes]:
        captured["arguments"] = arguments
        captured["wire"] = wire.read_bytes()
        return subprocess.CompletedProcess(arguments, 0, contract.canonical_json(response), b"")

    monkeypatch.setattr(client, "_snapshot_bundle", snapshot)
    monkeypatch.setattr(client.shutil, "which", lambda _: "ssh.exe")
    monkeypatch.setattr(client, "_run_ssh", run)
    result = client.process_bundle(
        bundle=tmp_path / "missing.zip",
        operation="validate",
        identity=identity,
        known_hosts=known_hosts,
        host=host,
    )
    assert result == response
    arguments = captured["arguments"]
    assert isinstance(arguments, list)
    assert "process-v1" in arguments
    assert "ConnectionAttempts=3" in arguments
    known_hosts_argument = next(
        item for item in arguments if item.startswith("UserKnownHostsFile=")
    )
    assert '"' in known_hosts_argument and "known hosts" in known_hosts_argument
    assert "\\" not in known_hosts_argument
    assert captured["wire"] == contract.canonical_json(
        {
            "archive_sha256": hashlib.sha256(b"bundle").hexdigest(),
            "operation": "validate",
            "purpose": "reviewed_ingestion",
            "schema_version": 1,
            "season_number": 1,
        }
    )


def test_client_rejects_bounded_or_unsanitized_remote_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = tmp_path / "identity"
    identity.write_bytes(b"x")
    identity.chmod(0o600)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(f"dev.example.invalid {_public_key()}\n", encoding="utf-8")
    monkeypatch.setattr(
        client,
        "_snapshot_bundle",
        lambda s, d: (1, "a" * 64),
    )
    monkeypatch.setattr(client.shutil, "which", lambda _: "ssh")
    monkeypatch.setattr(
        client,
        "_run_ssh",
        lambda args, wire: subprocess.CompletedProcess(
            args, 0, b"x" * (contract.PROCESS_STATUS_MAX_BYTES + 1), b""
        ),
    )
    with pytest.raises(client.ProcessingClientError):
        client.process_bundle(
            bundle=tmp_path / "bundle.zip",
            operation="validate",
            identity=identity,
            known_hosts=known_hosts,
            host="dev.example.invalid",
        )


def test_worker_command_is_no_shell_read_only_one_shot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = tmp_path / "release"
    (release / "deploy").mkdir(parents=True)
    (release / "deploy/compose.yaml").write_text("services: {}", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    observed: dict[str, object] = {}

    class FakeProcess:
        stdout = None
        stderr = None

    def popen(arguments: list[str], **kwargs: object) -> FakeProcess:
        observed["arguments"] = arguments
        observed["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(processor.subprocess, "Popen", popen)
    with pytest.raises(processor.ProcessingError):
        processor._run_worker(release, workspace)
    arguments = observed["arguments"]
    assert isinstance(arguments, list)
    assert arguments[2:4] == ["--progress", "quiet"]
    assert "--no-deps" in arguments
    assert "--no-TTY" in arguments
    assert arguments[arguments.index("--pull") + 1] == "never"
    assert "--user" in arguments
    assert f"{workspace.as_posix()}:/private-corpus:ro" in arguments
    assert observed["kwargs"]["shell"] is False


def test_worker_still_rejects_any_unexpected_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = tmp_path / "release"
    (release / "deploy").mkdir(parents=True)
    (release / "deploy/compose.yaml").write_text("services: {}", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    aggregate = {
        "episode_count": 24,
        "file_count": 25,
        "indexed_segment_count": 100,
        "mode": "ingest-reviewed",
        "purpose": "reviewed_ingestion",
        "season_number": 1,
        "total_bytes": 123,
    }

    class FakeProcess:
        stdout = io.BytesIO(contract.canonical_json(aggregate))
        stderr = io.BytesIO(b"unexpected worker warning\n")

        def wait(self, *, timeout: int) -> int:
            assert timeout == contract.PROCESSING_WORKER_TIMEOUT_SECONDS
            return 0

        def poll(self) -> int:
            return 0

        def kill(self) -> None:
            raise AssertionError("completed process must not be killed")

    monkeypatch.setattr(processor.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())

    with pytest.raises(processor.ProcessingError, match="worker failed"):
        processor._run_worker(release, workspace)


def _manifest() -> dict[str, object]:
    return {
        "purpose": "reviewed_ingestion",
        "season_number": 1,
        "file_count": 1,
        "total_bytes": 3,
        "files": [
            {
                "path": "Modern_Family/Season_01/Reviewed/episode.reviewed.srt",
                "size": 3,
                "sha256": hashlib.sha256(b"abc").hexdigest(),
            }
        ],
    }


def test_validate_never_materializes_runs_worker_or_writes_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _manifest()
    descriptor = processor.BundleFile(
        manifest["files"][0]["path"], 3, manifest["files"][0]["sha256"]
    )
    monkeypatch.setattr(processor, "_active_release", lambda: (Path("release"), object()))
    monkeypatch.setattr(processor, "_verify_release_image", lambda _release: None)
    monkeypatch.setattr(
        processor,
        "_verified_object",
        lambda digest, release: (Path("object"), manifest, (descriptor,)),
    )
    monkeypatch.setattr(
        processor,
        "_materialize",
        lambda *args: (_ for _ in ()).throw(AssertionError("materialized")),
    )
    monkeypatch.setattr(
        processor,
        "_run_worker",
        lambda *args: (_ for _ in ()).throw(AssertionError("worker")),
    )
    monkeypatch.setattr(
        processor,
        "_write_receipt",
        lambda *args: (_ for _ in ()).throw(AssertionError("receipt")),
    )
    result = processor.process_request(
        {
            "archive_sha256": "a" * 64,
            "operation": "validate",
            "purpose": "reviewed_ingestion",
            "schema_version": 1,
            "season_number": 1,
        }
    )
    assert result["status"] == "validated"
    assert result["mode"] == "validate"


def test_replay_returns_already_applied_without_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest()
    descriptor = processor.BundleFile(
        manifest["files"][0]["path"], 3, manifest["files"][0]["sha256"]
    )
    digest = "a" * 64
    aggregate = {
        "episode_count": 1,
        "file_count": 1,
        "indexed_segment_count": 4,
        "mode": "ingest-reviewed",
        "purpose": "reviewed_ingestion",
        "season_number": 1,
        "status": "applied",
        "total_bytes": 3,
    }
    receipt = {
        "archive_sha256": digest,
        "operation": "ingest-reviewed",
        "result": aggregate,
        "schema_version": 1,
    }
    monkeypatch.setattr(processor, "_active_release", lambda: (Path("release"), object()))
    monkeypatch.setattr(processor, "_verify_release_image", lambda _release: None)
    monkeypatch.setattr(
        processor,
        "_verified_object",
        lambda digest, release: (Path("object"), manifest, (descriptor,)),
    )
    monkeypatch.setattr(processor, "_ensure_receipts_root", lambda: None)
    monkeypatch.setattr(processor, "_materialize", lambda *args: tmp_path / "workspace")
    monkeypatch.setattr(processor, "_verify_workspace", lambda *args: None)
    monkeypatch.setattr(processor, "_load_receipt", lambda path: receipt)
    monkeypatch.setattr(processor.os.path, "lexists", lambda path: True)
    monkeypatch.setattr(
        processor,
        "_run_worker",
        lambda *args: (_ for _ in ()).throw(AssertionError("worker")),
    )
    result = processor.process_request(
        {
            "archive_sha256": digest,
            "operation": "ingest-reviewed",
            "purpose": "reviewed_ingestion",
            "schema_version": 1,
            "season_number": 1,
        }
    )
    assert result == {**aggregate, "status": "already_applied"}


def test_main_error_is_generic_and_request_has_exact_eof(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(processor.os, "name", "nt")
    assert processor.main() == 2
    captured = capsys.readouterr()
    assert "processing_rejected" in captured.err
    assert "private" not in captured.err
    with pytest.raises(processor.ProcessingError):
        processor._read_request(io.BytesIO(_request() + b"x"))


def test_release_image_binding_requires_matching_private_env_and_oci_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = tmp_path / ("a" * 40)
    release.mkdir()
    env_file = tmp_path / "dev.env"
    env_file.write_text(
        "\n".join(
            (
                "CINEGRAPH_ENVIRONMENT=development",
                f"CINEGRAPH_IMAGE={processor.host_contract.CINEGRAPH_IMAGE_NAME}",
                f"CINEGRAPH_IMAGE_DIGEST=sha256:{'b' * 64}",
                f"CINEGRAPH_RELEASE_SHA={release.name}",
                "OPENAI_API_KEY=never-read-by-this-test",
                "",
            )
        ),
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    monkeypatch.setattr(processor.host_contract, "DEV_ENV_FILE", env_file)
    monkeypatch.setattr(processor.receiver, "_root_owned", lambda _result: True)
    expected_reference = (
        f"{processor.host_contract.CINEGRAPH_IMAGE_NAME}@sha256:{'b' * 64}"
    )
    labels = {
        processor.host_contract.CINEGRAPH_IMAGE_REVISION_LABEL: release.name,
        processor.host_contract.CINEGRAPH_IMAGE_SOURCE_LABEL: (
            processor.host_contract.CINEGRAPH_IMAGE_SOURCE
        ),
        processor.host_contract.CINEGRAPH_IMAGE_VERSION_LABEL: f"sha-{release.name}",
    }
    completed = subprocess.CompletedProcess(
        [], 0, (json.dumps(labels) + "\n").encode("utf-8"), b""
    )
    observed: list[list[str]] = []

    def run(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed.append(arguments)
        return completed

    monkeypatch.setattr(processor.subprocess, "run", run)

    processor._verify_release_image(release)

    assert observed[0][3] == expected_reference


def test_release_image_binding_rejects_stale_release_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = tmp_path / ("a" * 40)
    release.mkdir()
    env_file = tmp_path / "dev.env"
    env_file.write_text(
        "\n".join(
            (
                f"CINEGRAPH_IMAGE={processor.host_contract.CINEGRAPH_IMAGE_NAME}",
                f"CINEGRAPH_IMAGE_DIGEST=sha256:{'b' * 64}",
                f"CINEGRAPH_RELEASE_SHA={'c' * 40}",
                "",
            )
        ),
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    monkeypatch.setattr(processor.host_contract, "DEV_ENV_FILE", env_file)
    monkeypatch.setattr(processor.receiver, "_root_owned", lambda _result: True)

    with pytest.raises(processor.ProcessingError, match="image"):
        processor._release_image_reference(release)
