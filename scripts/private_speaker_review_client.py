"""Pinned-SSH operator client for the offline private speaker-review worker."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import IO

_ROOT = Path(__file__).resolve().parents[1]
for _import_root in (_ROOT, _ROOT / "src"):
    if os.fspath(_import_root) not in sys.path:
        sys.path.insert(0, os.fspath(_import_root))

from cinegraph.common.private_corpus_bundle import BundleError, verify_bundle  # noqa: E402
from cinegraph.common.private_corpus_policy import (  # noqa: E402
    DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION,
)
from scripts import private_corpus_host_contract as host_contract  # noqa: E402
from scripts import private_speaker_review_contract as contract  # noqa: E402
from scripts.dev_host_contract import (  # noqa: E402
    known_hosts_line,
    validate_host,
    validate_public_key_line,
)


class SpeakerReviewClientError(RuntimeError):
    """A deliberately generic, path-free local or remote failure."""


def _is_reparse(result: os.stat_result) -> bool:
    return bool(getattr(result, "st_file_attributes", 0) & 0x400)


def _identity(result: os.stat_result) -> tuple[int, int, int, int, int]:
    return result.st_dev, result.st_ino, result.st_size, result.st_mtime_ns, result.st_nlink


def _regular(path: Path, *, private: bool = False) -> os.stat_result:
    try:
        result = path.lstat()
    except OSError as error:
        raise SpeakerReviewClientError("local input is unavailable") from error
    if (
        not stat.S_ISREG(result.st_mode)
        or stat.S_ISLNK(result.st_mode)
        or _is_reparse(result)
        or result.st_nlink != 1
        or result.st_size <= 0
        or (private and os.name != "nt" and stat.S_IMODE(result.st_mode) & 0o077)
    ):
        raise SpeakerReviewClientError("local input is not a safe regular file")
    return result


def _validate_known_hosts(path: Path, host: str) -> None:
    _regular(path)
    try:
        raw = path.read_text(encoding="utf-8")
        lines = raw.splitlines()
        if len(lines) != 1 or raw != lines[0] + "\n":
            raise ValueError
        configured_host, key_type, key_blob = lines[0].split(" ")
        public_key = f"{key_type} {key_blob}"
        validate_public_key_line(public_key)
        if configured_host != host or lines[0] != known_hosts_line(host, public_key):
            raise ValueError
    except (OSError, UnicodeError, ValueError) as error:
        raise SpeakerReviewClientError("known-hosts input is invalid") from error


def _ssh_config_path(path: Path) -> str:
    value = path.as_posix()
    if (
        not path.is_absolute()
        or not value
        or any(ord(character) < 32 for character in value)
        or any(character in value for character in ('"', "\\", "%", "$"))
    ):
        raise SpeakerReviewClientError("known-hosts input is invalid")
    return f'"{value}"'


def _snapshot_bundle(source: Path, destination: Path) -> tuple[int, str]:
    """Snapshot and verify a local speaker-review archive without sending it."""

    before = _regular(source)
    policy = DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION
    if before.st_size > policy.max_archive_bytes:
        raise SpeakerReviewClientError("bundle exceeds the review limit")
    descriptor = -1
    copied = 0
    digest = hashlib.sha256()
    try:
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with source.open("rb") as input_stream, os.fdopen(descriptor, "wb") as output_stream:
            descriptor = -1
            opened = os.fstat(input_stream.fileno())
            while chunk := input_stream.read(1024 * 1024):
                copied += len(chunk)
                if copied > policy.max_archive_bytes:
                    raise SpeakerReviewClientError("bundle exceeds the review limit")
                output_stream.write(chunk)
                digest.update(chunk)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        after = source.lstat()
    except SpeakerReviewClientError:
        raise
    except OSError as error:
        raise SpeakerReviewClientError("bundle snapshot failed") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if _identity(before) != _identity(opened) or _identity(opened) != _identity(after):
        raise SpeakerReviewClientError("bundle changed while it was snapshotted")
    if copied != opened.st_size:
        raise SpeakerReviewClientError("bundle changed while it was snapshotted")
    try:
        manifest, _ = verify_bundle(destination)
    except BundleError as error:
        raise SpeakerReviewClientError("bundle verification failed") from error
    if (
        manifest.get("purpose") != contract.SPEAKER_REVIEW_PURPOSE
        or type(manifest.get("season_number")) is not int
        or manifest["season_number"] != contract.SPEAKER_REVIEW_SEASON_NUMBER
    ):
        raise SpeakerReviewClientError("bundle is not the approved speaker-review season")
    return copied, digest.hexdigest()


def ssh_arguments(*, ssh: str, identity: Path, known_hosts: Path, host: str) -> list[str]:
    """Build the complete shell-free pinned SSH invocation."""

    return [
        ssh,
        "-F",
        "none",
        "-T",
        "-p",
        "22",
        "-i",
        os.fspath(identity),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={_ssh_config_path(known_hosts)}",
        "-o",
        f"GlobalKnownHostsFile={os.devnull}",
        "-o",
        "HostKeyAlgorithms=ssh-ed25519",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "PreferredAuthentications=publickey",
        "-o",
        "PubkeyAuthentication=yes",
        "-o",
        "ForwardAgent=no",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "RequestTTY=no",
        "-o",
        "Compression=no",
        "-o",
        "ControlMaster=no",
        "-o",
        "ControlPath=none",
        "-o",
        "ProxyCommand=none",
        "-o",
        "ProxyJump=none",
        "-o",
        "PermitLocalCommand=no",
        "-o",
        "CanonicalizeHostname=no",
        "-o",
        "VerifyHostKeyDNS=no",
        "-o",
        "UpdateHostKeys=no",
        "-o",
        "LogLevel=ERROR",
        "-o",
        f"ConnectionAttempts={contract.SPEAKER_REVIEW_SSH_CONNECTION_ATTEMPTS}",
        "-o",
        f"ConnectTimeout={contract.SPEAKER_REVIEW_SSH_CONNECT_TIMEOUT_SECONDS}",
        "-o",
        f"ServerAliveInterval={contract.SPEAKER_REVIEW_SSH_SERVER_ALIVE_INTERVAL_SECONDS}",
        "-o",
        f"ServerAliveCountMax={contract.SPEAKER_REVIEW_SSH_SERVER_ALIVE_COUNT_MAX}",
        f"{host_contract.CORPUS_USER}@{host}",
        contract.SPEAKER_REVIEW_COMMAND,
    ]


_ssh_arguments = ssh_arguments


def _read_pipe(stream: IO[bytes]) -> bytes:
    try:
        return stream.read(contract.SPEAKER_REVIEW_STATUS_MAX_BYTES + 1)
    finally:
        stream.close()


def _run_ssh(arguments: list[str], wire: Path) -> subprocess.CompletedProcess[bytes]:
    process: subprocess.Popen[bytes] | None = None
    try:
        with wire.open("rb") as source:
            process = subprocess.Popen(
                arguments,
                stdin=source,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
            if process.stdout is None or process.stderr is None:
                raise SpeakerReviewClientError("SSH speaker review failed")
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                stdout_future = executor.submit(_read_pipe, process.stdout)
                stderr_future = executor.submit(_read_pipe, process.stderr)
                try:
                    returncode = process.wait(
                        timeout=contract.SPEAKER_REVIEW_TIMEOUT_SECONDS
                        + contract.SPEAKER_REVIEW_KILL_AFTER_SECONDS
                        + contract.SPEAKER_REVIEW_CLIENT_TIMEOUT_MARGIN_SECONDS
                    )
                except subprocess.TimeoutExpired as error:
                    process.kill()
                    process.wait()
                    raise SpeakerReviewClientError("SSH speaker review failed") from error
                stdout = stdout_future.result(timeout=5)
                stderr = stderr_future.result(timeout=5)
        return subprocess.CompletedProcess(arguments, returncode, stdout, stderr)
    except SpeakerReviewClientError:
        raise
    except (OSError, subprocess.SubprocessError, TimeoutError) as error:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        raise SpeakerReviewClientError("SSH speaker review failed") from error


def review_bundle(
    *,
    bundle: Path | None,
    operation: str,
    identity: Path,
    known_hosts: Path,
    host: str,
    run_id: str | None = None,
    archive_sha256: str | None = None,
) -> dict[str, object]:
    """Validate or prepare a remote bundle using a digest-only request."""

    if operation not in contract.SPEAKER_REVIEW_OPERATIONS:
        raise SpeakerReviewClientError("operation input is invalid")
    if operation == "status":
        if run_id is None or archive_sha256 is None:
            raise SpeakerReviewClientError("status input is invalid")
        try:
            contract.validate_request(
                {
                    "archive_sha256": archive_sha256,
                    "operation": operation,
                    "purpose": contract.SPEAKER_REVIEW_PURPOSE,
                    "schema_version": contract.SPEAKER_REVIEW_PROTOCOL_VERSION,
                    "season_number": contract.SPEAKER_REVIEW_SEASON_NUMBER,
                    "run_id": run_id,
                }
            )
        except ValueError as error:
            raise SpeakerReviewClientError("status input is invalid") from error
        if bundle is not None:
            raise SpeakerReviewClientError("status bundle input is invalid")
        digest = archive_sha256
    else:
        if bundle is None:
            raise SpeakerReviewClientError("bundle input is required")
        if run_id is not None or archive_sha256 is not None:
            raise SpeakerReviewClientError("review input is invalid")
        digest = ""

    try:
        canonical_host = validate_host(host)
    except ValueError as error:
        raise SpeakerReviewClientError("host input is invalid") from error
    _regular(identity, private=True)
    _validate_known_hosts(known_hosts, canonical_host)
    ssh = shutil.which("ssh")
    if ssh is None:
        raise SpeakerReviewClientError("OpenSSH client is unavailable")

    with tempfile.TemporaryDirectory(prefix="cinegraph-speaker-review-") as temp_name:
        temp = Path(temp_name)
        if os.name != "nt":
            temp.chmod(0o700)
        if bundle is not None:
            _archive_bytes, digest = _snapshot_bundle(bundle, temp / "bundle.zip")
        request_value: dict[str, object] = {
            "archive_sha256": digest,
            "operation": operation,
            "purpose": contract.SPEAKER_REVIEW_PURPOSE,
            "schema_version": contract.SPEAKER_REVIEW_PROTOCOL_VERSION,
            "season_number": contract.SPEAKER_REVIEW_SEASON_NUMBER,
        }
        if operation == "status":
            request_value["run_id"] = run_id
        request = contract.canonical_json(request_value)
        contract.parse_request(request)
        wire = temp / "request.json"
        wire.write_bytes(request)
        completed = _run_ssh(
            ssh_arguments(ssh=ssh, identity=identity, known_hosts=known_hosts, host=canonical_host),
            wire,
        )
        stdout = completed.stdout or b""
        stderr = completed.stderr or b""
        if (
            completed.returncode != 0
            or stderr
            or len(stdout) > contract.SPEAKER_REVIEW_STATUS_MAX_BYTES
            or len(stderr) > contract.SPEAKER_REVIEW_STATUS_MAX_BYTES
        ):
            raise SpeakerReviewClientError("remote speaker review was rejected")
        try:
            result = contract.parse_aggregate(stdout, operation=operation)
        except (TypeError, ValueError) as error:
            raise SpeakerReviewClientError("remote response is invalid") from error
        return result


prepare_bundle = review_bundle
speaker_review = review_bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--archive-sha256")
    parser.add_argument(
        "--operation", required=True, choices=sorted(contract.SPEAKER_REVIEW_OPERATIONS)
    )
    parser.add_argument("--run-id")
    parser.add_argument("--identity", required=True, type=Path)
    parser.add_argument("--known-hosts", required=True, type=Path)
    parser.add_argument("--host", required=True)
    args = parser.parse_args(argv)
    try:
        result = review_bundle(
            bundle=args.bundle,
            archive_sha256=args.archive_sha256,
            operation=args.operation,
            run_id=args.run_id,
            identity=args.identity,
            known_hosts=args.known_hosts,
            host=args.host,
        )
    except SpeakerReviewClientError:
        sys.stderr.buffer.write(
            contract.canonical_json({"error": "speaker_review_rejected", "status": "error"})
        )
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0
