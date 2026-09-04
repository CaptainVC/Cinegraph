"""Windows-safe operator client for the private-corpus processing boundary."""

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
from scripts.dev_host_contract import (  # noqa: E402
    known_hosts_line,
    validate_host,
    validate_public_key_line,
)
from scripts.private_corpus_processing_contract import (  # noqa: E402
    PROCESS_COMMAND,
    PROCESS_OPERATIONS,
    PROCESS_PROTOCOL_VERSION,
    PROCESS_PURPOSE,
    PROCESS_REQUEST_MAX_BYTES,
    PROCESS_STATUS_MAX_BYTES,
    PROCESSING_CLIENT_TIMEOUT_MARGIN_SECONDS,
    PROCESSING_SSH_CONNECT_TIMEOUT_SECONDS,
    PROCESSING_SSH_CONNECTION_ATTEMPTS,
    PROCESSING_SSH_SERVER_ALIVE_COUNT_MAX,
    PROCESSING_SSH_SERVER_ALIVE_INTERVAL_SECONDS,
    canonical_json,
    validate_aggregate,
)


class ProcessingClientError(RuntimeError):
    """A deliberately generic local or remote processing failure."""


def _is_reparse(result: os.stat_result) -> bool:
    return bool(getattr(result, "st_file_attributes", 0) & 0x400)


def _identity(result: os.stat_result) -> tuple[int, int, int, int, int]:
    return result.st_dev, result.st_ino, result.st_size, result.st_mtime_ns, result.st_nlink


def _regular(path: Path, *, private: bool = False) -> os.stat_result:
    try:
        result = path.lstat()
    except OSError as error:
        raise ProcessingClientError("local input is unavailable") from error
    if (
        not stat.S_ISREG(result.st_mode)
        or stat.S_ISLNK(result.st_mode)
        or _is_reparse(result)
        or result.st_nlink != 1
        or result.st_size <= 0
        or (private and os.name != "nt" and stat.S_IMODE(result.st_mode) & 0o077)
    ):
        raise ProcessingClientError("local input is not a safe regular file")
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
        raise ProcessingClientError("known-hosts input is invalid") from error


def _ssh_config_path(path: Path) -> str:
    """Quote a path for OpenSSH's second config-tokenization pass."""

    value = path.as_posix()
    if (
        not path.is_absolute()
        or not value
        or any(ord(character) < 32 for character in value)
        or any(character in value for character in ('"', "\\", "%", "$"))
    ):
        raise ProcessingClientError("known-hosts input is invalid")
    return f'"{value}"'


def _snapshot_bundle(source: Path, destination: Path) -> tuple[int, str]:
    before = _regular(source)
    policy = DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION
    if before.st_size > policy.max_archive_bytes:
        raise ProcessingClientError("bundle exceeds the processing limit")
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
                    raise ProcessingClientError("bundle exceeds the processing limit")
                output_stream.write(chunk)
                digest.update(chunk)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        after = source.lstat()
    except ProcessingClientError:
        raise
    except OSError as error:
        raise ProcessingClientError("bundle snapshot failed") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if _identity(before) != _identity(opened) or _identity(opened) != _identity(after):
        raise ProcessingClientError("bundle changed while it was snapshotted")
    if copied != opened.st_size:
        raise ProcessingClientError("bundle changed while it was snapshotted")
    try:
        manifest, _ = verify_bundle(destination)
    except BundleError as error:
        raise ProcessingClientError("bundle verification failed") from error
    if (
        manifest.get("purpose") != PROCESS_PURPOSE
        or type(manifest.get("season_number")) is not int
        or manifest["season_number"] != 1
    ):
        raise ProcessingClientError("bundle is not the approved reviewed season")
    del manifest
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
        f"ConnectionAttempts={PROCESSING_SSH_CONNECTION_ATTEMPTS}",
        "-o",
        f"ConnectTimeout={PROCESSING_SSH_CONNECT_TIMEOUT_SECONDS}",
        "-o",
        f"ServerAliveInterval={PROCESSING_SSH_SERVER_ALIVE_INTERVAL_SECONDS}",
        "-o",
        f"ServerAliveCountMax={PROCESSING_SSH_SERVER_ALIVE_COUNT_MAX}",
        f"{host_contract.CORPUS_USER}@{host}",
        PROCESS_COMMAND,
    ]


# Keep the private helper spelling parallel to the transfer client API.
_ssh_arguments = ssh_arguments


def _read_pipe(stream: IO[bytes]) -> bytes:
    try:
        return stream.read(PROCESS_STATUS_MAX_BYTES + 1)
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
                raise ProcessingClientError("SSH processing failed")
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                stdout_future = executor.submit(_read_pipe, process.stdout)
                stderr_future = executor.submit(_read_pipe, process.stderr)
                try:
                    returncode = process.wait(
                        timeout=host_contract.PROCESSING_TIMEOUT_SECONDS
                        + host_contract.PROCESSING_KILL_AFTER_SECONDS
                        + PROCESSING_CLIENT_TIMEOUT_MARGIN_SECONDS
                    )
                except subprocess.TimeoutExpired as error:
                    process.kill()
                    process.wait()
                    raise ProcessingClientError("SSH processing failed") from error
                stdout = stdout_future.result(timeout=5)
                stderr = stderr_future.result(timeout=5)
        return subprocess.CompletedProcess(arguments, returncode, stdout, stderr)
    except ProcessingClientError:
        raise
    except (OSError, subprocess.SubprocessError, TimeoutError) as error:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        raise ProcessingClientError("SSH processing failed") from error


def process_bundle(
    *, bundle: Path, operation: str, identity: Path, known_hosts: Path, host: str
) -> dict[str, object]:
    if operation not in PROCESS_OPERATIONS:
        raise ProcessingClientError("operation input is invalid")
    try:
        canonical_host = validate_host(host)
    except ValueError as error:
        raise ProcessingClientError("host input is invalid") from error
    _regular(identity, private=True)
    _validate_known_hosts(known_hosts, canonical_host)
    ssh = shutil.which("ssh")
    if ssh is None:
        raise ProcessingClientError("OpenSSH client is unavailable")
    with tempfile.TemporaryDirectory(prefix="cinegraph-corpus-processing-") as temp_name:
        temp = Path(temp_name)
        if os.name != "nt":
            temp.chmod(0o700)
        snapshot = temp / "bundle.zip"
        _archive_bytes, archive_sha256 = _snapshot_bundle(bundle, snapshot)
        request = canonical_json(
            {
                "archive_sha256": archive_sha256,
                "operation": operation,
                "purpose": PROCESS_PURPOSE,
                "schema_version": PROCESS_PROTOCOL_VERSION,
                "season_number": 1,
            }
        )
        if len(request) > PROCESS_REQUEST_MAX_BYTES:
            raise ProcessingClientError("processing request is too large")
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
            or len(stdout) > PROCESS_STATUS_MAX_BYTES
            or len(stderr) > PROCESS_STATUS_MAX_BYTES
        ):
            raise ProcessingClientError("remote processing was rejected")
        try:

            def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
                result: dict[str, object] = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate key")
                    result[key] = value
                return result

            decoded = json.loads(
                stdout.decode("utf-8"),
                object_pairs_hook=reject_duplicates,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
            expected_mode = operation
            expected_statuses = (
                {"validated"}
                if operation == "validate"
                else {
                    "applied",
                    "already_applied",
                }
            )
            if not isinstance(decoded, dict) or decoded.get("status") not in expected_statuses:
                raise ValueError("invalid status")
            result = validate_aggregate(decoded, mode=expected_mode, status=str(decoded["status"]))
        except (UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise ProcessingClientError("remote response is invalid") from error
        if canonical_json(result) != stdout:
            raise ProcessingClientError("remote response is invalid")
        return result


_process_private_corpus = process_bundle
process_private_corpus = process_bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--operation", required=True, choices=sorted(PROCESS_OPERATIONS))
    parser.add_argument("--identity", required=True, type=Path)
    parser.add_argument("--known-hosts", required=True, type=Path)
    parser.add_argument("--host", required=True)
    args = parser.parse_args(argv)
    try:
        result = process_bundle(
            bundle=args.bundle,
            operation=args.operation,
            identity=args.identity,
            known_hosts=args.known_hosts,
            host=args.host,
        )
    except ProcessingClientError:
        sys.stderr.buffer.write(canonical_json({"error": "processing_rejected", "status": "error"}))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0
