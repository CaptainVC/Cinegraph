from __future__ import annotations

import base64
import errno
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import private_corpus_host_contract as host_contract
from scripts import receive_private_corpus as receiver
from scripts import transfer_private_corpus as client
from scripts.build_private_corpus_bundle import _selection

from cinegraph.adapters.catalogue import JsonCatalogueManifestLoader
from cinegraph.common.private_corpus_bundle import build_bundle
from cinegraph.config import (
    DEFAULT_CORPUS_LAYOUT,
    DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION,
    DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
)


def _public_key() -> str:
    blob = b"\x00\x00\x00\x0bssh-ed25519" + b"\x00\x00\x00 " + b"x" * 32
    return "ssh-ed25519 " + base64.b64encode(blob).decode("ascii")


def _loaded_catalogue():
    return JsonCatalogueManifestLoader().load(Path("knowledge/catalogue.json"))


def _receiver_catalogue() -> receiver.CatalogueSnapshot:
    return receiver._parse_catalogue(Path("knowledge/catalogue.json").read_bytes())


def _speaker_bundle(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    loaded = _loaded_catalogue()
    selected = _selection(
        knowledge_root=source,
        loaded=loaded,
        purpose="speaker_review",
        season_number=2,
    )
    for index, relative in enumerate(selected):
        path = source.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"synthetic corpus fixture {index}".encode())
    output = tmp_path / "bundle.zip"
    build_bundle(
        source_root=source,
        output_archive=output,
        purpose="speaker_review",
        selected_paths=selected,
        catalogue_sha256=loaded.content_sha256,
        season_number=2,
    )
    return output


def _reviewed_bundle(tmp_path: Path, *, corrupt_ledger_hash: bool = False) -> Path:
    source = tmp_path / "reviewed-source"
    source.mkdir()
    loaded = _loaded_catalogue()
    selected = sorted(
        receiver._expected_selection(
            _receiver_catalogue(),
            "reviewed_ingestion",
            1,
        )
    )
    subtitle_paths = [item for item in selected if item.endswith(".srt")]
    records: list[dict[str, object]] = []
    for index, relative in enumerate(subtitle_paths, start=1):
        path = source.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        content = f"{index}\n00:00:00,000 --> 00:00:01,000\nNarrator: fixture {index}\n".encode()
        path.write_bytes(content)
        reviewed_hash = hashlib.sha256(content).hexdigest()
        if corrupt_ledger_hash and index == 1:
            reviewed_hash = "0" * 64
        records.append(
            {
                "candidate_filename": path.name.replace(".reviewed.srt", ".script-aligned.srt"),
                "reviewed_filename": path.name,
                "candidate_sha256": "1" * 64,
                "reviewed_sha256": reviewed_hash,
                "promoted_question_mark_labels": 0,
                "removed_redaction_lines": 0,
                "removed_cue_numbers": [],
            }
        )
    ledger_path = next(item for item in selected if item.endswith("review-ledger.json"))
    source.joinpath(*ledger_path.split("/")).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "review_status": "reviewed",
                "reviewed_by": "fixture-reviewer",
                "reviewed_at": "2026-01-01T00:00:00+00:00",
                "records": records,
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    output = tmp_path / ("bad-reviewed.zip" if corrupt_ledger_hash else "reviewed.zip")
    build_bundle(
        source_root=source,
        output_archive=output,
        purpose="reviewed_ingestion",
        selected_paths=selected,
        catalogue_sha256=loaded.content_sha256,
        season_number=1,
    )
    return output


def _wire(archive: Path, *, digest: str | None = None, size: int | None = None) -> bytes:
    content = archive.read_bytes()
    import hashlib

    header = host_contract.canonical_json(
        {
            "archive_bytes": len(content) if size is None else size,
            "archive_sha256": hashlib.sha256(content).hexdigest() if digest is None else digest,
            "protocol": 1,
        }
    )
    return header + content


def _host_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = {
        "DEV_PRIVATE_CORPUS_ROOT": tmp_path / "private/dev",
        "TRANSACTIONS_ROOT": tmp_path / "private/dev/transactions",
        "OBJECTS_ROOT": tmp_path / "private/dev/objects",
        "QUARANTINE_ROOT": tmp_path / "private/dev/quarantine",
    }
    for path in roots.values():
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
    for name, path in roots.items():
        monkeypatch.setattr(host_contract, name, path)

    capacity = SimpleNamespace(
        f_bavail=10 * 1024 * 1024,
        f_frsize=4096,
        f_favail=100_000,
    )
    monkeypatch.setattr(receiver.os, "statvfs", lambda _: capacity, raising=False)
    # The production receiver requires real root ownership. CI intentionally runs
    # unprivileged, so this fixture supplies the already-verified ownership boundary
    # while exercising the remaining filesystem and transaction behavior.
    monkeypatch.setattr(receiver, "_root_owned", lambda _: True)
    monkeypatch.setattr(receiver, "_active_catalogue", _receiver_catalogue)
    monkeypatch.setattr(receiver, "_require_host_hierarchy", lambda: None)
    monkeypatch.setattr(receiver, "_fsync_tree", lambda _: None)
    monkeypatch.setattr(
        receiver, "_rename_no_replace", lambda source, target: source.rename(target)
    )


def test_canonical_header_is_accepted_without_consuming_archive() -> None:
    raw = host_contract.canonical_json(
        {"archive_bytes": 3, "archive_sha256": "a" * 64, "protocol": 1}
    )
    stream = io.BytesIO(raw + b"zip")
    assert receiver.read_transfer_header(stream) == receiver.TransferHeader(3, "a" * 64, 1)
    assert stream.read() == b"zip"


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{}",
        b"{}\n",
        b'{"archive_bytes":1,"archive_bytes":1,"archive_sha256":"'
        + b"a" * 64
        + b'","protocol":1}\n',
        b'{"archive_bytes":true,"archive_sha256":"' + b"a" * 64 + b'","protocol":1}\n',
        b'{"archive_bytes":1,"archive_sha256":"' + b"A" * 64 + b'","protocol":1}\n',
        b'{"archive_bytes":1,"archive_sha256":"' + b"a" * 64 + b'","protocol":2}\n',
        b'{"archive_bytes":1,"archive_sha256":"' + b"a" * 64 + b'","extra":1,"protocol":1}\n',
        b"x" * (host_contract.HEADER_MAX_BYTES + 1),
        b"\xef\xbb\xbf{}\n",
    ],
)
def test_malformed_headers_are_rejected(raw: bytes) -> None:
    with pytest.raises(receiver.TransferError, match="invalid_header"):
        receiver.read_transfer_header(io.BytesIO(raw))


def test_header_rejects_archive_over_limit() -> None:
    maximum = DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION.max_archive_bytes
    raw = host_contract.canonical_json(
        {"archive_bytes": maximum + 1, "archive_sha256": "a" * 64, "protocol": 1}
    )
    with pytest.raises(receiver.TransferError, match="invalid_header"):
        receiver.read_transfer_header(io.BytesIO(raw))


def test_stdlib_catalogue_parser_reproduces_application_digest_and_selection() -> None:
    snapshot = _receiver_catalogue()
    application = _loaded_catalogue()
    assert snapshot.content_sha256 == application.content_sha256
    assert frozenset(snapshot.reviewed_filenames_by_season) == frozenset({1, 2})
    assert tuple(map(len, snapshot.reviewed_filenames_by_season.values())) == (24, 24)


def test_stdlib_bundle_policy_is_the_application_layout_source_of_truth() -> None:
    policy = DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION
    layout = DEFAULT_CORPUS_LAYOUT
    assert policy.review_ledger_filename == layout.review_ledger_filename
    assert policy.season_directory_suffix == layout.season_directory_suffix
    assert policy.reviewed_directory_name == layout.reviewed_directory_name
    assert policy.aligned_directory_name == layout.aligned_directory_name
    assert policy.reviewed_subtitle_suffix == layout.reviewed_subtitle_suffix
    assert policy.aligned_subtitle_suffix == layout.aligned_subtitle_suffix
    assert (
        policy.script_pdf_filename_template
        == DEFAULT_SPEAKER_REVIEW_CONFIGURATION.script_pdf_filename_template
    )


@pytest.mark.parametrize("mutation", ["duplicate", "nan", "uuid", "season", "filename"])
def test_stdlib_catalogue_parser_rejects_noncanonical_policy_inputs(mutation: str) -> None:
    raw = Path("knowledge/catalogue.json").read_bytes()
    if mutation == "duplicate":
        raw = raw.replace(b'"schema_version": 1,', b'"schema_version": 1, "schema_version": 1,', 1)
    elif mutation == "nan":
        raw = raw.replace(b'"schema_version": 1', b'"schema_version": NaN', 1)
    else:
        payload = json.loads(raw)
        series = payload["series"][0]
        if mutation == "uuid":
            series["series_id"] = host_contract.CANONICAL_SERIES_ID.replace("-", "")
        elif mutation == "season":
            series["seasons"][1]["season_number"] = 3
        else:
            series["seasons"][0]["episodes"][0]["reviewed_subtitle_filename"] = "../x.reviewed.srt"
        raw = json.dumps(payload).encode()
    with pytest.raises(receiver.TransferError, match="catalogue_mismatch"):
        receiver._parse_catalogue(raw)


@pytest.mark.parametrize(
    ("body", "size", "digest", "message"),
    [
        (b"ab", 3, "a" * 64, "incomplete_stream"),
        (b"abcd", 3, "a" * 64, "trailing_stream"),
        (b"abc", 3, "a" * 64, "digest_mismatch"),
    ],
)
def test_received_stream_requires_exact_size_digest_and_eof(
    tmp_path: Path, body: bytes, size: int, digest: str, message: str
) -> None:
    header = receiver.TransferHeader(size, digest, 1)
    with pytest.raises(receiver.TransferError, match=message):
        receiver._write_received_archive(io.BytesIO(body), tmp_path / "received", header)


def test_capacity_fails_before_a_transaction_is_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host_roots(tmp_path, monkeypatch)
    capacity = SimpleNamespace(f_bavail=1, f_frsize=1, f_favail=1)
    monkeypatch.setattr(receiver.os, "statvfs", lambda _: capacity, raising=False)
    raw = host_contract.canonical_json(
        {"archive_bytes": 1, "archive_sha256": "a" * 64, "protocol": 1}
    )
    with pytest.raises(receiver.TransferError, match="host_capacity"):
        receiver.receive_private_corpus(io.BytesIO(raw + b"x"))
    assert not any(host_contract.TRANSACTIONS_ROOT.iterdir())


def test_receive_installs_and_exact_replay_is_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _speaker_bundle(tmp_path)
    _host_roots(tmp_path, monkeypatch)
    first = receiver.receive_private_corpus(io.BytesIO(_wire(archive)))
    assert first == {
        "file_count": 25,
        "purpose": "speaker_review",
        "season_number": 2,
        "status": "installed",
        "total_bytes": sum(
            len(f"synthetic corpus fixture {index}".encode()) for index in range(25)
        ),
    }
    objects = list(host_contract.OBJECTS_ROOT.iterdir())
    assert len(objects) == 1
    final = objects[0]
    before = final.stat()
    receipt_before = (final / host_contract.INSTALL_RECEIPT_FILENAME).read_bytes()

    second = receiver.receive_private_corpus(io.BytesIO(_wire(archive)))
    assert second["status"] == "already_present"
    after = final.stat()
    assert (before.st_dev, before.st_ino, before.st_mtime_ns) == (
        after.st_dev,
        after.st_ino,
        after.st_mtime_ns,
    )
    assert (final / host_contract.INSTALL_RECEIPT_FILENAME).read_bytes() == receipt_before
    assert not any(host_contract.TRANSACTIONS_ROOT.iterdir())


def test_reviewed_receive_independently_validates_ledger_and_srt_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _reviewed_bundle(tmp_path)
    _host_roots(tmp_path, monkeypatch)
    result = receiver.receive_private_corpus(io.BytesIO(_wire(archive)))
    assert result["purpose"] == "reviewed_ingestion"
    assert result["status"] == "installed"


def test_reviewed_receive_rejects_ledger_hash_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _reviewed_bundle(tmp_path, corrupt_ledger_hash=True)
    _host_roots(tmp_path, monkeypatch)
    with pytest.raises(receiver.TransferError, match="catalogue_mismatch"):
        receiver.receive_private_corpus(io.BytesIO(_wire(archive)))
    assert not any(host_contract.OBJECTS_ROOT.iterdir())


def test_corrupt_replay_is_never_repaired_or_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _speaker_bundle(tmp_path)
    _host_roots(tmp_path, monkeypatch)
    receiver.receive_private_corpus(io.BytesIO(_wire(archive)))
    final = next(host_contract.OBJECTS_ROOT.iterdir())
    receipt = final / host_contract.INSTALL_RECEIPT_FILENAME
    receipt.write_bytes(b"corrupt")
    before = final.stat()

    with pytest.raises(receiver.TransferError, match="object_integrity"):
        receiver.receive_private_corpus(io.BytesIO(_wire(archive)))
    after = final.stat()
    assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
    assert receipt.read_bytes() == b"corrupt"


def test_catalogue_digest_mismatch_does_not_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _speaker_bundle(tmp_path)
    _host_roots(tmp_path, monkeypatch)
    monkeypatch.setattr(
        receiver,
        "_active_catalogue",
        lambda: receiver.CatalogueSnapshot(
            content_sha256="0" * 64,
            reviewed_filenames_by_season=_receiver_catalogue().reviewed_filenames_by_season,
        ),
    )
    with pytest.raises(receiver.TransferError, match="catalogue_mismatch"):
        receiver.receive_private_corpus(io.BytesIO(_wire(archive)))
    assert not any(host_contract.OBJECTS_ROOT.iterdir())
    assert not any(host_contract.TRANSACTIONS_ROOT.iterdir())


@pytest.mark.parametrize(
    "payload",
    [
        b"not-a-zip",
        b"PK\x03\x04truncated",
    ],
)
def test_invalid_or_partial_archive_cleans_transaction_and_never_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: bytes
) -> None:
    import hashlib

    _host_roots(tmp_path, monkeypatch)
    header = host_contract.canonical_json(
        {
            "archive_bytes": len(payload),
            "archive_sha256": hashlib.sha256(payload).hexdigest(),
            "protocol": 1,
        }
    )
    with pytest.raises(receiver.TransferError, match="bundle_rejected"):
        receiver.receive_private_corpus(io.BytesIO(header + payload))
    assert not any(host_contract.OBJECTS_ROOT.iterdir())
    assert not any(host_contract.TRANSACTIONS_ROOT.iterdir())


def test_short_full_transfer_cleans_transaction_and_never_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _speaker_bundle(tmp_path)
    _host_roots(tmp_path, monkeypatch)
    with pytest.raises(receiver.TransferError, match="incomplete_stream"):
        receiver.receive_private_corpus(io.BytesIO(_wire(archive)[:-1]))
    assert not any(host_contract.OBJECTS_ROOT.iterdir())
    assert not any(host_contract.TRANSACTIONS_ROOT.iterdir())


def test_concurrent_exact_publish_race_verifies_winner_without_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _speaker_bundle(tmp_path)
    _host_roots(tmp_path, monkeypatch)

    def race(source: Path, target: Path) -> None:
        shutil.copytree(source, target)
        raise FileExistsError(errno.EEXIST, "simulated exact winner")

    monkeypatch.setattr(receiver, "_rename_no_replace", race)
    result = receiver.receive_private_corpus(io.BytesIO(_wire(archive)))
    assert result["status"] == "already_present"
    assert len(list(host_contract.OBJECTS_ROOT.iterdir())) == 1
    assert not any(host_contract.TRANSACTIONS_ROOT.iterdir())


def test_replay_rejects_extra_file_and_hardlinked_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _speaker_bundle(tmp_path)
    _host_roots(tmp_path, monkeypatch)
    receiver.receive_private_corpus(io.BytesIO(_wire(archive)))
    final = next(host_contract.OBJECTS_ROOT.iterdir())
    extra = final / "unexpected.srt"
    extra.write_bytes(b"unexpected")
    with pytest.raises(receiver.TransferError, match="object_integrity"):
        receiver.receive_private_corpus(io.BytesIO(_wire(archive)))
    extra.unlink()

    member = next(final.rglob("*.srt"))
    outside_link = host_contract.DEV_PRIVATE_CORPUS_ROOT / "member-hardlink"
    try:
        os.link(member, outside_link)
    except OSError:
        pytest.skip("hardlinks are unavailable")
    with pytest.raises(receiver.TransferError, match="object_integrity"):
        receiver.receive_private_corpus(io.BytesIO(_wire(archive)))


def test_cleanup_refuses_replaced_transaction_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host_roots(tmp_path, monkeypatch)
    transaction = host_contract.TRANSACTIONS_ROOT / ".receive-owned"
    transaction.mkdir()
    metadata = transaction.stat()
    transaction.rename(host_contract.TRANSACTIONS_ROOT / ".receive-original")
    transaction.mkdir()
    marker = transaction / "preserve"
    marker.write_text("unrelated", encoding="utf-8")
    receiver._cleanup_transaction(transaction, (metadata.st_dev, metadata.st_ino))
    assert marker.read_text(encoding="utf-8") == "unrelated"


def test_client_streams_canonical_header_and_exact_binary_without_a_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _speaker_bundle(tmp_path)
    identity = tmp_path / "identity"
    identity.write_bytes(b"private-test-fixture")
    identity.chmod(0o600)
    host = "dev.example.invalid"
    known_hosts = tmp_path / "known hosts"
    known_hosts.write_text(f"{host} {_public_key()}\n", encoding="utf-8")
    observed: dict[str, object] = {}
    response = host_contract.canonical_json(
        {
            "file_count": 25,
            "purpose": "speaker_review",
            "season_number": 2,
            "status": "installed",
            "total_bytes": 123,
        }
    )

    def run(arguments: list[str], wire_path: Path) -> subprocess.CompletedProcess[bytes]:
        observed["wire"] = wire_path.read_bytes()
        observed["arguments"] = arguments
        return subprocess.CompletedProcess(arguments, 0, response, b"")

    monkeypatch.setattr(client.shutil, "which", lambda _: "C:/Windows/System32/OpenSSH/ssh.exe")
    monkeypatch.setattr(client, "_run_ssh", run)
    result = client.transfer_bundle(
        bundle=archive,
        identity=identity,
        known_hosts=known_hosts,
        host=host,
    )
    assert result["status"] == "installed"
    arguments = observed["arguments"]
    assert isinstance(arguments, list)
    assert arguments[-1] == host_contract.RECEIVE_COMMAND
    assert f"{host_contract.CORPUS_USER}@{host}" in arguments
    assert str(archive) not in arguments
    assert f'UserKnownHostsFile="{known_hosts.as_posix()}"' in arguments
    for option in (
        "StrictHostKeyChecking=yes",
        "IdentitiesOnly=yes",
        "BatchMode=yes",
        "ClearAllForwardings=yes",
        "ForwardAgent=no",
        "RequestTTY=no",
        "Compression=no",
        "ControlMaster=no",
        "ProxyCommand=none",
        "ProxyJump=none",
        "HostKeyAlgorithms=ssh-ed25519",
    ):
        assert option in arguments
    source = Path("scripts/transfer_private_corpus.py").read_text(encoding="utf-8")
    assert "subprocess.Popen(" in source
    assert "shell=False" in source
    assert "capture_output=True" not in source
    wire = observed["wire"]
    assert isinstance(wire, bytes)
    header, body = wire.split(b"\n", 1)
    decoded_header = json.loads(header)
    assert decoded_header["archive_bytes"] == len(body)
    assert body == archive.read_bytes()


def test_client_rejects_noncanonical_known_hosts_without_calling_ssh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = tmp_path / "identity"
    identity.write_bytes(b"fixture")
    identity.chmod(0o600)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("changed.example.invalid " + _public_key() + "\n", encoding="utf-8")
    called = False

    def run(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(client.subprocess, "run", run)
    with pytest.raises(client.ClientTransferError, match="known-hosts"):
        client.transfer_bundle(
            bundle=tmp_path / "absent.zip",
            identity=identity,
            known_hosts=known_hosts,
            host="dev.example.invalid",
        )
    assert called is False


@pytest.mark.parametrize("unsafe_name", ['known"hosts', "known%hosts", "known$hosts"])
def test_client_rejects_open_ssh_config_token_paths(
    tmp_path: Path, unsafe_name: str
) -> None:
    with pytest.raises(client.ClientTransferError, match="known-hosts"):
        client._ssh_config_path(tmp_path / unsafe_name)


def test_client_rejects_unbounded_or_malformed_remote_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = tmp_path / "identity"
    identity.write_bytes(b"fixture")
    identity.chmod(0o600)
    known_hosts = tmp_path / "known_hosts"
    host = "dev.example.invalid"
    known_hosts.write_text(f"{host} {_public_key()}\n", encoding="utf-8")
    monkeypatch.setattr(client.shutil, "which", lambda _: "ssh")

    def snapshot(_source: Path, destination: Path) -> tuple[int, str]:
        destination.write_bytes(b"x")
        return 1, hashlib.sha256(b"x").hexdigest()

    monkeypatch.setattr(client, "_snapshot_bundle", snapshot)

    def oversized(arguments: list[str], _wire: Path) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            arguments,
            0,
            b"x" * (host_contract.STATUS_MAX_BYTES + 1),
            b"",
        )

    monkeypatch.setattr(client, "_run_ssh", oversized)
    with pytest.raises(client.ClientTransferError, match="rejected"):
        client.transfer_bundle(
            bundle=tmp_path / "private-location.zip",
            identity=identity,
            known_hosts=known_hosts,
            host=host,
        )


def test_ssh_capture_is_bounded_while_child_is_running(tmp_path: Path) -> None:
    wire = tmp_path / "wire.bin"
    wire.write_bytes(b"")
    amount = host_contract.STATUS_MAX_BYTES * 8
    result = client._run_ssh(
        [
            sys.executable,
            "-c",
            (
                "import sys;"
                f"sys.stdout.buffer.write(b'x'*{amount});"
                f"sys.stderr.buffer.write(b'y'*{amount})"
            ),
        ],
        wire,
    )
    assert len(result.stdout) == host_contract.STATUS_MAX_BYTES + 1
    assert len(result.stderr) == host_contract.STATUS_MAX_BYTES + 1


def test_receiver_main_redacts_nested_exception_text(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(receiver.os, "name", "posix")
    monkeypatch.setattr(receiver.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(receiver.sys, "stdin", SimpleNamespace(buffer=io.BytesIO()))

    def fail(_stream: object) -> dict[str, object]:
        raise RuntimeError("C:/private/key.txt and corpus bytes must not escape")

    monkeypatch.setattr(receiver, "receive_private_corpus", fail)
    assert receiver.main() == 2
    captured = capfd.readouterr()
    assert captured.out == ""
    assert captured.err == '{"error":"transfer_rejected","status":"error"}\n'
    assert "key.txt" not in captured.err


def test_receiver_sigterm_is_translated_to_cleanup_error() -> None:
    with pytest.raises(receiver.TransferError, match="receive_interrupted"):
        receiver._terminate_receive(15, None)


def test_receiver_starts_under_stdlib_only_isolated_python() -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "scripts/receive_private_corpus.py"],
        input=b"",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert result.stdout == b""
    assert result.stderr == b'{"error":"transfer_rejected","status":"error"}\n'
    assert b"ImportError" not in result.stderr
    assert b"ModuleNotFoundError" not in result.stderr


def test_active_catalogue_parses_the_securely_read_release_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    releases = tmp_path / "releases"
    releases.mkdir()
    release = releases / ("a" * 40)
    catalogue = release / "knowledge/catalogue.json"
    catalogue.parent.mkdir(parents=True)
    catalogue.write_bytes(Path("knowledge/catalogue.json").read_bytes())
    catalogue.chmod(0o644)
    current = tmp_path / "current"
    try:
        current.symlink_to(release, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    monkeypatch.setattr(host_contract, "RELEASES_ROOT", releases)
    monkeypatch.setattr(host_contract, "CURRENT_LINK", current)
    monkeypatch.setattr(receiver, "_root_owned", lambda _: True)
    assert receiver._active_catalogue() == _receiver_catalogue()


def test_static_forced_command_and_helper_are_separate_and_bounded() -> None:
    dispatcher = Path("deploy/remote/corpus-dispatch.sh").read_text(encoding="utf-8")
    helper = Path("deploy/remote/receive-private-corpus.sh").read_text(encoding="utf-8")
    process_helper = Path("deploy/remote/process-private-corpus.sh").read_text(
        encoding="utf-8"
    )
    deployment = Path("deploy/remote/deploy-dispatch.sh").read_text(encoding="utf-8")
    quality = Path(".github/workflows/quality.yml").read_text(encoding="utf-8")

    assert "receive-v1)" in dispatcher
    assert "process-v1)" in dispatcher
    assert "sudo -n /usr/local/sbin/cinegraph-receive-private-corpus" in dispatcher
    assert "sudo -n /usr/local/sbin/cinegraph-process-private-corpus" in dispatcher
    assert "eval" not in dispatcher
    assert "bash -c" not in dispatcher
    assert "scp" not in dispatcher.lower()
    assert "sftp" not in dispatcher.lower()
    assert "cinegraph-deploy" not in dispatcher
    assert "receive-v1" not in deployment
    assert helper.index('exec 8>"$TRANSFER_LOCK"') < helper.index('exec 9>"$DEPLOYMENT_LOCK"')
    assert "env -i PATH=/usr/sbin:/usr/bin" in helper
    assert 'python3 -I -S -B "$receiver"' in helper
    assert "docker" not in helper
    assert "OPENAI_API_KEY" not in helper
    assert process_helper.index('exec 8>"$TRANSFER_LOCK"') < process_helper.index(
        'exec 9>"$DEPLOYMENT_LOCK"'
    ) < process_helper.index('exec 7>"$PROCESSING_LOCK"')
    assert 'python3 -I -S -B "$processor"' in process_helper
    assert "SUDO_USER=cinegraph-corpus" in process_helper
    assert "refs/remotes/origin/main" in process_helper
    assert "scripts/run_private_corpus_processing.py" in process_helper
    assert "OPENAI_API_KEY" not in process_helper
    assert "archive_sha256" not in process_helper
    assert "eval" not in process_helper
    assert "bash -c" not in process_helper
    assert "corpus-dispatch.sh" in quality
    assert "receive-private-corpus.sh" in quality
    assert "process-private-corpus.sh" in quality


def test_shell_boundaries_parse_when_bash_is_available() -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is unavailable")
    result = subprocess.run(
        [
            bash,
            "-n",
            "deploy/remote/corpus-dispatch.sh",
            "deploy/remote/receive-private-corpus.sh",
            "deploy/remote/process-private-corpus.sh",
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0 and "CreateProcessCommon" in result.stderr:
        pytest.skip("Windows WSL bash shim is unavailable")
    assert result.returncode == 0
