from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts import private_speaker_review_contract as contract
from scripts import run_private_speaker_review as processor

from cinegraph.common.private_corpus_bundle import BundleFile


def _request(operation: str = "validate", **changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "archive_sha256": "a" * 64,
        "operation": operation,
        "purpose": contract.REVIEW_PURPOSE,
        "schema_version": contract.REVIEW_PROTOCOL_VERSION,
        "season_number": contract.REVIEW_SEASON_NUMBER,
    }
    if operation == "status":
        value["run_id"] = "speaker-review-0123456789abcdef"
    value.update(changes)
    return value


def _manifest(*, purpose: str = "speaker_review", season: int = 2) -> dict[str, object]:
    return {
        "purpose": purpose,
        "season_number": season,
        "file_count": 2,
        "total_bytes": 12,
        "source_catalogue_sha256": "b" * 64,
        "files": [],
    }


def test_source_file_contract_binds_install_receipt_content() -> None:
    content = b"canonical install receipt"
    descriptor = BundleFile(
        ".install-receipt.json",
        len(content),
        hashlib.sha256(content).hexdigest(),
    )

    expected = processor._source_files((), descriptor)

    assert expected[".install-receipt.json"] == descriptor


def test_source_file_is_private_until_copy_is_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_bytes(b"private source")
    observed_modes: list[int] = []
    real_open = processor.os.open

    def open_file(path: object, flags: int, mode: int | None = None) -> int:
        if mode is not None:
            observed_modes.append(mode)
            return real_open(path, flags, mode)
        return real_open(path, flags)

    monkeypatch.setattr(processor.os, "open", open_file)
    monkeypatch.setattr(processor, "_set_owner", lambda *_args: None)

    processor._copy_source_file(source, destination, expected=None)

    assert observed_modes == [processor._SOURCE_STAGING_FILE_MODE]
    if os.name != "nt":
        assert destination.stat().st_mode & 0o777 == processor._SOURCE_FILE_MODE


def _aggregate(*, operation: str = "prepare", status: str = "prepared") -> dict[str, object]:
    return {
        "candidate_count": 4,
        "estimated_primary_cost_usd": 0.123456,
        "file_count": 2,
        "operation": operation,
        "primary_part_count": 2,
        "purpose": contract.REVIEW_PURPOSE,
        "run_id": "speaker-review-0123456789abcdef",
        "season_number": contract.REVIEW_SEASON_NUMBER,
        "status": status,
        "total_bytes": 12,
    }


def _install_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    release = tmp_path / ("c" * 40)
    release.mkdir()
    monkeypatch.setattr(processor.host_runtime, "_active_release", lambda: (release, object()))
    monkeypatch.setattr(processor.host_runtime, "_verify_release_image", lambda _: None)
    monkeypatch.setattr(
        processor.host_runtime,
        "_release_image_reference",
        lambda _: "ghcr.io/cinegraph@sha256:" + "d" * 64,
    )
    monkeypatch.setattr(
        processor,
        "_configuration_sha256",
        lambda _release=None: "e" * 64,
    )


def test_request_reader_requires_canonical_input_and_exact_eof() -> None:
    raw = contract.canonical_json(_request())
    assert processor._read_request(io.BytesIO(raw)) == _request()
    for invalid in (raw + b"x", raw.replace(b"\n", b"\r\n"), raw[:-1]):
        with pytest.raises(processor.SpeakerReviewProcessingError, match="invalid request"):
            processor._read_request(io.BytesIO(invalid))


def test_request_reader_rejects_oversize_and_duplicate_input() -> None:
    oversized = b"{" + b"x" * contract.REVIEW_REQUEST_MAX_BYTES
    duplicate = (
        b'{"archive_sha256":"'
        + b"a" * 64
        + b'","archive_sha256":"'
        + b"a" * 64
        + b'","operation":"validate","purpose":"speaker_review",'
        b'"schema_version":1,"season_number":2}\n'
    )
    for raw in (oversized, duplicate):
        with pytest.raises(processor.SpeakerReviewProcessingError, match="invalid request"):
            processor._read_request(io.BytesIO(raw))


def test_root_entrypoint_loads_without_site_packages() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "scripts/run_private_speaker_review.py",
        ],
        input=b"",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        check=False,
        timeout=10,
    )

    assert completed.returncode == 2
    assert completed.stdout == b""
    assert completed.stderr == contract.canonical_json(
        {"error": "speaker_review_rejected", "status": "error"}
    )


def test_validate_does_not_materialize_run_worker_or_write_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_runtime(monkeypatch, tmp_path)
    manifest = _manifest()
    monkeypatch.setattr(
        processor,
        "_verified_object",
        lambda _: (tmp_path / "object", manifest, tuple(), object()),
    )
    for name in ("_materialize_source", "_run_worker", "_write_receipt"):
        monkeypatch.setattr(
            processor,
            name,
            lambda *args, _name=name, **kwargs: (_ for _ in ()).throw(AssertionError(_name)),
        )
    result = processor.process_request(_request())
    assert result == {
        "file_count": 2,
        "operation": "validate",
        "purpose": contract.REVIEW_PURPOSE,
        "season_number": 2,
        "status": "validated",
        "total_bytes": 12,
    }


def test_prepare_success_verifies_source_and_run_before_writing_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_runtime(monkeypatch, tmp_path)
    manifest = _manifest()
    receipt_path = tmp_path / "receipt.json"
    calls: list[str] = []
    aggregate = _aggregate()
    monkeypatch.setattr(
        processor,
        "_verified_object",
        lambda _: (tmp_path / "object", manifest, tuple(), object()),
    )
    monkeypatch.setattr(processor, "_receipt_path", lambda _: receipt_path)
    monkeypatch.setattr(processor, "_materialize_source", lambda *args: tmp_path / "source")
    monkeypatch.setattr(processor, "_run_mount", lambda _: tmp_path / "review-runs")
    monkeypatch.setattr(
        processor,
        "_verify_source_workspace",
        lambda *args: calls.append("source"),
    )
    monkeypatch.setattr(
        processor,
        "_run_worker",
        lambda *args: (calls.append("worker") or aggregate),
    )
    monkeypatch.setattr(
        processor,
        "_validate_persisted_run",
        lambda *args: (calls.append("run") or ("f" * 64, 5)),
    )
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        processor,
        "_write_receipt",
        lambda path, payload: observed.update(path=path, payload=payload),
    )
    result = processor.process_request(_request("prepare"))
    assert result == aggregate
    assert calls == ["source", "worker", "source", "run"]
    assert observed["path"] == receipt_path
    assert observed["payload"]["result"] == aggregate


def test_exact_prepare_replay_is_already_prepared_without_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_runtime(monkeypatch, tmp_path)
    digest = "a" * 64
    manifest = _manifest()
    aggregate = _aggregate()
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_bytes(b"receipt")
    monkeypatch.setattr(
        processor,
        "_verified_object",
        lambda _: (tmp_path / "object", manifest, tuple(), object()),
    )
    monkeypatch.setattr(processor, "_receipt_path", lambda _: receipt_path)
    monkeypatch.setattr(
        processor,
        "_load_receipt",
        lambda _: {"artifact_file_count": 5, "artifact_set_sha256": "f" * 64},
    )
    monkeypatch.setattr(processor, "_verify_receipt_binding", lambda *args, **kwargs: aggregate)
    monkeypatch.setattr(processor, "_materialize_source", lambda *args: tmp_path / "source")
    monkeypatch.setattr(processor, "_verify_source_workspace", lambda *args: None)
    monkeypatch.setattr(processor, "_run_mount", lambda _: tmp_path / "review-runs")
    monkeypatch.setattr(
        processor,
        "_validate_persisted_run",
        lambda *args: ("f" * 64, 5),
    )
    monkeypatch.setattr(
        processor,
        "_run_worker",
        lambda *args: (_ for _ in ()).throw(AssertionError("replay must not run worker")),
    )
    assert processor.process_request(_request("prepare")) == {
        **aggregate,
        "operation": "prepare",
        "status": "already_prepared",
    }
    assert digest == _request("prepare")["archive_sha256"]


def test_status_binds_requested_run_id_and_rejects_other_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_runtime(monkeypatch, tmp_path)
    manifest = _manifest()
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_bytes(b"receipt")
    aggregate = _aggregate()
    monkeypatch.setattr(
        processor,
        "_verified_object",
        lambda _: (tmp_path / "object", manifest, tuple(), object()),
    )
    monkeypatch.setattr(processor, "_receipt_path", lambda _: receipt_path)
    monkeypatch.setattr(
        processor,
        "_load_receipt",
        lambda _: {"artifact_file_count": 5, "artifact_set_sha256": "f" * 64},
    )
    monkeypatch.setattr(processor, "_verify_receipt_binding", lambda *args, **kwargs: aggregate)
    monkeypatch.setattr(processor, "_materialize_source", lambda *args: tmp_path / "source")
    monkeypatch.setattr(processor, "_verify_source_workspace", lambda *args: None)
    monkeypatch.setattr(processor, "_run_mount", lambda _: tmp_path / "review-runs")
    monkeypatch.setattr(
        processor,
        "_validate_persisted_run",
        lambda *args: ("f" * 64, 5),
    )
    assert processor.process_request(_request("status")) == {
        **aggregate,
        "operation": "status",
        "status": "prepared",
    }
    with pytest.raises(processor.SpeakerReviewProcessingError, match="receipt invalid"):
        processor.process_request(_request("status", run_id="speaker-review-fedcba9876543210"))


def test_receipt_binding_covers_release_catalogue_configuration_and_image(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    release = tmp_path / ("e" * 40)
    release.mkdir()
    manifest = _manifest()
    aggregate = _aggregate()
    image = "ghcr.io/cinegraph@sha256:" + "f" * 64
    monkeypatch.setattr(
        processor,
        "_configuration_sha256",
        lambda _release=None: "e" * 64,
    )
    receipt = processor._expected_receipt(
        digest="a" * 64,
        manifest=manifest,
        release=release,
        image_reference=image,
        result=aggregate,
        artifact_set_sha256="f" * 64,
        artifact_file_count=5,
    )
    assert (
        processor._verify_receipt_binding(
            receipt,
            digest="a" * 64,
            manifest=manifest,
            release=release,
            image_reference=image,
        )
        == aggregate
    )
    for key in (
        "archive_sha256",
        "artifact_file_count",
        "artifact_set_sha256",
        "catalogue_sha256",
        "configuration_sha256",
        "image_reference",
        "release_sha",
    ):
        altered = dict(receipt)
        altered[key] = "tampered"
        with pytest.raises(processor.SpeakerReviewProcessingError, match="receipt invalid"):
            processor._verify_receipt_binding(
                altered,
                digest="a" * 64,
                manifest=manifest,
                release=release,
                image_reference=image,
            )
    monkeypatch.setattr(
        processor,
        "_configuration_sha256",
        lambda _release=None: "0" * 64,
    )
    with pytest.raises(processor.SpeakerReviewProcessingError, match="receipt invalid"):
        processor._verify_receipt_binding(
            receipt,
            digest="a" * 64,
            manifest=manifest,
            release=release,
            image_reference=image,
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership and mode contract")
def test_persisted_run_binds_exact_prepared_artifact_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(processor.host_contract, "SPEAKER_REVIEW_UID", os.getuid())
    monkeypatch.setattr(processor.host_contract, "SPEAKER_REVIEW_GID", os.getgid())
    review_runs = tmp_path / "review-runs"
    run_id = "speaker-review-0123456789abcdef"
    run_directory = review_runs / run_id
    run_directory.mkdir(parents=True, mode=0o700)
    run_directory.chmod(0o700)
    aggregate = _aggregate()
    state = {
        "candidate_count": aggregate["candidate_count"],
        "estimated_primary_cost_usd": aggregate["estimated_primary_cost_usd"],
        "primary_part_count": aggregate["primary_part_count"],
        "run_id": run_id,
        "status": "prepared",
    }
    artifacts = {
        "candidates.jsonl": b'{"candidate_id":"private"}\n',
        "primary-part-0001-requests.jsonl": b'{"custom_id":"one"}\n',
        "primary-part-0002-requests.jsonl": b'{"custom_id":"two"}\n',
        "run-state.json": (json.dumps(state, sort_keys=True) + "\n").encode("utf-8"),
        "source-manifest.json": b'{"schema_version":1,"sources":{}}\n',
    }
    for name, content in artifacts.items():
        path = run_directory / name
        path.write_bytes(content)
        path.chmod(0o600)

    first_digest, first_count = processor._validate_persisted_run(
        review_runs,
        aggregate,
    )
    assert first_count == 5
    assert len(first_digest) == 64

    candidate_path = run_directory / "candidates.jsonl"
    candidate_path.write_bytes(b'{"candidate_id":"changed"}\n')
    candidate_path.chmod(0o600)
    changed_digest, changed_count = processor._validate_persisted_run(
        review_runs,
        aggregate,
    )
    assert changed_count == first_count
    assert changed_digest != first_digest

    unexpected = run_directory / "unexpected.json"
    unexpected.write_text("{}\n", encoding="utf-8")
    unexpected.chmod(0o600)
    with pytest.raises(processor.SpeakerReviewProcessingError, match="prepared run invalid"):
        processor._validate_persisted_run(review_runs, aggregate)


def test_worker_uses_separate_read_only_source_and_writable_run_mounts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    release = tmp_path / "release"
    (release / "deploy").mkdir(parents=True)
    source = tmp_path / "immutable-source"
    runs = tmp_path / "review-runs"
    source.mkdir()
    runs.mkdir()
    aggregate = _aggregate()

    class Process:
        stdout = io.BytesIO(contract.canonical_json(aggregate))
        stderr = io.BytesIO()

        def wait(self, *, timeout: int) -> int:
            assert timeout == processor._WORKER_TIMEOUT_SECONDS
            return 0

        def poll(self) -> int:
            return 0

    observed: dict[str, object] = {}

    def popen(arguments: list[str], **kwargs: object) -> Process:
        observed.update(arguments=arguments, kwargs=kwargs)
        return Process()

    monkeypatch.setattr(processor.subprocess, "Popen", popen)
    monkeypatch.setattr(processor, "_cleanup_compose_worker", lambda *_args: None)
    assert processor._run_worker(release, source, runs) == aggregate
    arguments = observed["arguments"]
    assert f"{source.as_posix()}:/private-corpus:ro" in arguments
    assert f"{runs.as_posix()}:/private-corpus/review-runs:rw" in arguments
    assert observed["kwargs"]["shell"] is False


def test_worker_precleans_stale_named_container_before_retry_and_after_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events: list[str] = []

    class Process:
        stdout = io.BytesIO(contract.canonical_json(_aggregate()))
        stderr = io.BytesIO()

        def wait(self, *, timeout: int) -> int:
            del timeout
            return 0

        def poll(self) -> int:
            return 0

    def popen(*args: object, **kwargs: object) -> Process:
        del args, kwargs
        events.append("popen")
        return Process()

    monkeypatch.setattr(processor.subprocess, "Popen", popen)
    monkeypatch.setattr(
        processor,
        "_cleanup_compose_worker",
        lambda *_args: events.append("cleanup"),
    )

    assert processor._run_worker(tmp_path, tmp_path / "source", tmp_path / "runs") == _aggregate()
    assert events[0] == "cleanup"
    assert events[-1] == "cleanup"
    assert events.index("popen") > 0


def test_worker_cleans_named_container_after_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events: list[str] = []

    class Process:
        stdout = io.BytesIO()
        stderr = io.BytesIO()

        def wait(self, *, timeout: int) -> int:
            del timeout
            return 17

        def poll(self) -> int:
            return 17

    monkeypatch.setattr(processor.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(
        processor,
        "_cleanup_compose_worker",
        lambda *_args: events.append("cleanup"),
    )

    with pytest.raises(processor.SpeakerReviewProcessingError, match="worker failed"):
        processor._run_worker(tmp_path, tmp_path / "source", tmp_path / "runs")
    assert events[0] == "cleanup"
    assert events[-1] == "cleanup"
    assert len(events) >= 2


@pytest.mark.parametrize(
    "stdout,stderr,returncode",
    [
        (b"not-json\n", b"", 0),
        (contract.canonical_json({**_aggregate(), "path": "/secret"}), b"", 0),
        (contract.canonical_json(_aggregate()), b"unexpected\n", 0),
        (b"x" * (contract.REVIEW_OUTPUT_MAX_BYTES + 1), b"", 0),
        (b"", b"y" * (contract.REVIEW_OUTPUT_MAX_BYTES + 1), 0),
        (b"", b"", 137),
    ],
)
def test_worker_rejects_malformed_oversize_stderr_secret_and_failure_output(
    stdout: bytes,
    stderr: bytes,
    returncode: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Process:
        def __init__(self) -> None:
            self.stdout = io.BytesIO(stdout)
            self.stderr = io.BytesIO(stderr)

        def wait(self, *, timeout: int) -> int:
            del timeout
            return returncode

        def poll(self) -> int:
            return returncode

    monkeypatch.setattr(processor.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(processor, "_cleanup_compose_worker", lambda *_args: None)
    with pytest.raises(processor.SpeakerReviewProcessingError, match="worker failed"):
        processor._run_worker(tmp_path, tmp_path / "source", tmp_path / "runs")


def test_worker_timeout_kills_child_and_does_not_leak_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events: list[str] = []
    monkeypatch.setattr(processor.subprocess, "run", lambda *args, **kwargs: None)

    class Process:
        stdout = io.BytesIO()
        stderr = io.BytesIO()

        def wait(self, *, timeout: int | None = None) -> int:
            del timeout
            events.append("wait")
            if events.count("wait") == 1:
                raise subprocess.TimeoutExpired("docker compose", 1)
            return -9

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            events.append("kill")

    monkeypatch.setattr(processor.subprocess, "Popen", lambda *args, **kwargs: Process())
    with pytest.raises(processor.SpeakerReviewProcessingError, match="worker failed") as error:
        processor._run_worker(tmp_path / "release", tmp_path / "source", tmp_path / "runs")
    assert str(error.value) == "worker failed"
    assert events == ["wait", "kill", "wait"]


def test_worker_timeout_terminates_compose_process_group_on_posix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events: list[tuple[str, int, int | None]] = []

    class Process:
        pid = 4242
        stdout = io.BytesIO()
        stderr = io.BytesIO()

        def wait(self, *, timeout: int | None = None) -> int:
            events.append(("wait", self.pid, timeout))
            if len(events) == 1:
                raise subprocess.TimeoutExpired("docker compose", 1)
            if len(events) == 2:
                raise subprocess.TimeoutExpired("docker compose", 1)
            return -9

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            events.append(("terminate", self.pid, None))

        def kill(self) -> None:
            events.append(("kill", self.pid, None))

    signals: list[tuple[int, int]] = []
    cleanup: list[list[str]] = []
    monkeypatch.setattr(processor.os, "name", "posix")
    monkeypatch.setattr(
        processor.os,
        "killpg",
        lambda process_group_id, signal_number: signals.append((process_group_id, signal_number)),
        raising=False,
    )
    monkeypatch.setattr(processor.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(processor.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(
        processor.subprocess,
        "run",
        lambda arguments, **kwargs: cleanup.append(arguments),
    )

    with pytest.raises(processor.SpeakerReviewProcessingError, match="worker failed"):
        processor._run_worker(tmp_path / "release", tmp_path / "source", tmp_path / "runs")

    assert signals == [(4242, processor.signal.SIGTERM), (4242, 9)]
    assert events == [
        ("wait", 4242, processor._WORKER_TIMEOUT_SECONDS),
        ("wait", 4242, processor._WORKER_TERMINATION_GRACE_SECONDS),
        ("wait", 4242, None),
    ]
    assert cleanup[0][-3:] == [
        "--force",
        "--stop",
        "corpus-speaker-review-prepare",
    ]
    assert cleanup[1] == [
        "docker",
        "rm",
        "--force",
        processor.host_contract.SPEAKER_REVIEW_CONTAINER_NAME,
    ]


def test_worker_starts_compose_in_a_new_session_on_posix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    observed: dict[str, object] = {}

    class Process:
        pid = 4243
        stdout = io.BytesIO(contract.canonical_json(_aggregate()))
        stderr = io.BytesIO()

        def wait(self, *, timeout: int) -> int:
            del timeout
            return 0

        def poll(self) -> int:
            return 0

    def popen(arguments: list[str], **kwargs: object) -> Process:
        del arguments
        observed.update(kwargs)
        return Process()

    monkeypatch.setattr(processor.os, "name", "posix")
    monkeypatch.setattr(processor.subprocess, "Popen", popen)
    monkeypatch.setattr(processor.subprocess, "run", lambda *args, **kwargs: None)
    assert processor._run_worker(tmp_path, tmp_path / "source", tmp_path / "runs") == _aggregate()
    assert observed["start_new_session"] is True


def test_main_error_payload_is_generic_and_does_not_expose_secret_or_paths(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(processor.os, "name", "nt")
    assert processor.main() == 2
    captured = capsys.readouterr()
    assert json.loads(captured.err) == {"error": "speaker_review_rejected", "status": "error"}
    assert "OPENAI_API_KEY" not in captured.err
    assert str(Path.cwd()) not in captured.err


def test_no_receipt_is_written_when_worker_or_persisted_run_verification_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_runtime(monkeypatch, tmp_path)
    manifest = _manifest()
    receipt_path = tmp_path / "receipt.json"
    monkeypatch.setattr(
        processor,
        "_verified_object",
        lambda _: (tmp_path / "object", manifest, tuple(), object()),
    )
    monkeypatch.setattr(processor, "_receipt_path", lambda _: receipt_path)
    monkeypatch.setattr(processor, "_materialize_source", lambda *args: tmp_path / "source")
    monkeypatch.setattr(processor, "_run_mount", lambda _: tmp_path / "runs")
    monkeypatch.setattr(processor, "_verify_source_workspace", lambda *args: None)
    monkeypatch.setattr(
        processor,
        "_run_worker",
        lambda *args: _aggregate(),
    )
    monkeypatch.setattr(
        processor,
        "_validate_persisted_run",
        lambda *args: (_ for _ in ()).throw(
            processor.SpeakerReviewProcessingError("prepared run invalid")
        ),
    )
    monkeypatch.setattr(
        processor,
        "_write_receipt",
        lambda *args: (_ for _ in ()).throw(AssertionError("receipt written too soon")),
    )
    with pytest.raises(processor.SpeakerReviewProcessingError, match="prepared run invalid"):
        processor.process_request(_request("prepare"))
    assert not receipt_path.exists()


def test_object_verification_rejects_wrong_purpose_and_season_before_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(processor.receiver, "_regular_root_file", lambda *args, **kwargs: b"{}")
    monkeypatch.setattr(processor, "_decode_json", lambda _: {"archive_sha256": "a" * 64})
    monkeypatch.setattr(
        processor, "_decode_manifest", lambda _: _manifest(purpose="reviewed_ingestion", season=1)
    )
    monkeypatch.setattr(processor.receiver, "_verify_object", lambda *args: None)
    monkeypatch.setattr(processor.receiver, "_validate_catalogue_selection", lambda *args: None)
    with pytest.raises(processor.SpeakerReviewProcessingError, match="private object rejected"):
        processor._verified_object("a" * 64)
