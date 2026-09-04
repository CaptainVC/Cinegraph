"""Binary-safe operator client for one pinned-SSH Dev corpus transfer."""

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

_REPOSITORY_IMPORT_ROOT = Path(__file__).resolve().parents[1]
if os.fspath(_REPOSITORY_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(_REPOSITORY_IMPORT_ROOT))

from cinegraph.common.private_corpus_bundle import BundleError, verify_bundle  # noqa: E402
from cinegraph.common.private_corpus_policy import (  # noqa: E402
    DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION,
)
from scripts.dev_host_contract import (  # noqa: E402
    known_hosts_line,
    validate_host,
    validate_public_key_line,
)
from scripts.private_corpus_host_contract import (  # noqa: E402
    ALLOWED_SCHEMA_V1_SEASONS,
    CORPUS_USER,
    RECEIVE_COMMAND,
    STATUS_MAX_BYTES,
    TRANSFER_PROTOCOL_VERSION,
    TRANSFER_TIMEOUT_SECONDS,
    canonical_json,
)


class ClientTransferError(RuntimeError):
    """A deliberately path-free local transfer failure."""


def _is_reparse(result: os.stat_result) -> bool:
    return bool(getattr(result, "st_file_attributes", 0) & 0x400)


def _identity(result: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        result.st_size,
        result.st_mtime_ns,
        result.st_nlink,
    )


def _require_regular(
    path: Path, *, private: bool = False, allow_empty: bool = False
) -> os.stat_result:
    try:
        result = path.lstat()
    except OSError as error:
        raise ClientTransferError("local input is unavailable") from error
    if (
        not stat.S_ISREG(result.st_mode)
        or stat.S_ISLNK(result.st_mode)
        or _is_reparse(result)
        or result.st_nlink != 1
        or (result.st_size <= 0 and not allow_empty)
    ):
        raise ClientTransferError("local input is not a safe regular file")
    if private and os.name != "nt" and stat.S_IMODE(result.st_mode) & 0o077:
        raise ClientTransferError("local identity file permissions are not private")
    return result


def _validate_known_hosts(path: Path, host: str) -> None:
    _require_regular(path)
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
        raise ClientTransferError("known-hosts input is invalid") from error


def _ssh_config_path(path: Path) -> str:
    """Quote one absolute path for OpenSSH's second config-tokenization pass."""

    value = path.as_posix()
    if (
        not path.is_absolute()
        or not value
        or any(ord(character) < 32 for character in value)
        or any(character in value for character in ('"', "\\", "%", "$"))
    ):
        raise ClientTransferError("known-hosts input is invalid")
    return f'"{value}"'


def _snapshot_bundle(source: Path, destination: Path) -> tuple[int, str]:
    before = _require_regular(source)
    policy_limit = DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION.max_archive_bytes
    if before.st_size > policy_limit:
        raise ClientTransferError("bundle exceeds the transfer limit")
    descriptor = -1
    digest = hashlib.sha256()
    try:
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with (
            source.open("rb") as input_stream,
            os.fdopen(descriptor, "wb", closefd=True) as output_stream,
        ):
            descriptor = -1
            opened = os.fstat(input_stream.fileno())
            copied = 0
            while chunk := input_stream.read(1024 * 1024):
                copied += len(chunk)
                if copied > policy_limit:
                    raise ClientTransferError("bundle exceeds the transfer limit")
                output_stream.write(chunk)
                digest.update(chunk)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        after = source.lstat()
    except ClientTransferError:
        raise
    except OSError as error:
        raise ClientTransferError("bundle snapshot failed") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        _identity(before) != _identity(opened)
        or _identity(opened) != _identity(after)
        or copied != opened.st_size
    ):
        raise ClientTransferError("bundle changed while it was snapshotted")
    try:
        verify_bundle(destination)
    except BundleError as error:
        raise ClientTransferError("bundle verification failed") from error
    return copied, digest.hexdigest()


def _ssh_arguments(*, ssh: str, identity: Path, known_hosts: Path, host: str) -> list[str]:
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
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=2",
        f"{CORPUS_USER}@{host}",
        RECEIVE_COMMAND,
    ]


def _read_bounded_pipe(stream: IO[bytes]) -> bytes:
    try:
        return stream.read(STATUS_MAX_BYTES + 1)
    finally:
        stream.close()


def _run_ssh(arguments: list[str], wire: Path) -> subprocess.CompletedProcess[bytes]:
    """Run SSH with concurrent, strictly bounded stdout and stderr capture."""

    process: subprocess.Popen[bytes] | None = None
    try:
        with wire.open("rb") as wire_input:
            process = subprocess.Popen(
                arguments,
                stdin=wire_input,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
            if process.stdout is None or process.stderr is None:
                raise ClientTransferError("SSH transfer failed")
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                stdout_future = executor.submit(_read_bounded_pipe, process.stdout)
                stderr_future = executor.submit(_read_bounded_pipe, process.stderr)
                try:
                    returncode = process.wait(timeout=TRANSFER_TIMEOUT_SECONDS + 30)
                except subprocess.TimeoutExpired as error:
                    process.kill()
                    process.wait()
                    raise ClientTransferError("SSH transfer failed") from error
                stdout = stdout_future.result(timeout=5)
                stderr = stderr_future.result(timeout=5)
        return subprocess.CompletedProcess(arguments, returncode, stdout, stderr)
    except ClientTransferError:
        raise
    except (OSError, subprocess.SubprocessError, TimeoutError) as error:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        raise ClientTransferError("SSH transfer failed") from error


def transfer_bundle(
    *, bundle: Path, identity: Path, known_hosts: Path, host: str
) -> dict[str, object]:
    try:
        canonical_host = validate_host(host)
    except ValueError as error:
        raise ClientTransferError("host input is invalid") from error
    _require_regular(identity, private=True)
    _validate_known_hosts(known_hosts, canonical_host)
    ssh = shutil.which("ssh")
    if ssh is None:
        raise ClientTransferError("OpenSSH client is unavailable")

    with tempfile.TemporaryDirectory(prefix="cinegraph-corpus-transfer-") as temp_name:
        temp = Path(temp_name)
        if os.name != "nt":
            temp.chmod(0o700)
        snapshot = temp / "bundle.zip"
        archive_bytes, archive_sha256 = _snapshot_bundle(bundle, snapshot)
        header = canonical_json(
            {
                "archive_bytes": archive_bytes,
                "archive_sha256": archive_sha256,
                "protocol": TRANSFER_PROTOCOL_VERSION,
            }
        )
        wire = temp / "wire.bin"
        descriptor = os.open(wire, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        copied = 0
        copied_digest = hashlib.sha256()
        with (
            os.fdopen(descriptor, "wb", closefd=True) as wire_output,
            snapshot.open("rb") as source,
        ):
            snapshot_before = snapshot.lstat()
            snapshot_opened = os.fstat(source.fileno())
            wire_output.write(header)
            while chunk := source.read(1024 * 1024):
                wire_output.write(chunk)
                copied += len(chunk)
                copied_digest.update(chunk)
            wire_output.flush()
            os.fsync(wire_output.fileno())
        snapshot_after = snapshot.lstat()
        if (
            _identity(snapshot_before) != _identity(snapshot_opened)
            or _identity(snapshot_opened) != _identity(snapshot_after)
            or copied != archive_bytes
            or copied_digest.hexdigest() != archive_sha256
        ):
            raise ClientTransferError("bundle snapshot changed before transfer")
        wire_before = _require_regular(wire)
        arguments = _ssh_arguments(
            ssh=ssh,
            identity=identity,
            known_hosts=known_hosts,
            host=canonical_host,
        )
        try:
            with wire.open("rb") as wire_input:
                wire_opened = os.fstat(wire_input.fileno())
            completed = _run_ssh(arguments, wire)
            wire_after = wire.lstat()
        except (OSError, subprocess.SubprocessError) as error:
            raise ClientTransferError("SSH transfer failed") from error
        if (
            _identity(wire_before) != _identity(wire_opened)
            or _identity(wire_opened) != _identity(wire_after)
        ):
            raise ClientTransferError("transfer snapshot changed during SSH")
        stdout = completed.stdout
        stderr = completed.stderr
        if stdout is None or stderr is None:
            raise ClientTransferError("remote response is invalid")
        if len(stdout) > STATUS_MAX_BYTES or len(stderr) > STATUS_MAX_BYTES:
            raise ClientTransferError("remote transfer was rejected")
        if completed.returncode != 0 or stderr:
            raise ClientTransferError("remote transfer was rejected")
        try:
            decoded = json.loads(
                stdout.decode("utf-8"),
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
        except (UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise ClientTransferError("remote response is invalid") from error
        policy = DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION
        if (
            not isinstance(decoded, dict)
            or set(decoded)
            != {"file_count", "purpose", "season_number", "status", "total_bytes"}
            or decoded.get("status") not in {"installed", "already_present"}
            or decoded.get("purpose") not in policy.allowed_purposes
            or type(decoded.get("season_number")) is not int
            or decoded.get("season_number") not in ALLOWED_SCHEMA_V1_SEASONS
            or type(decoded.get("file_count")) is not int
            or not 0 < decoded["file_count"] <= policy.max_file_count
            or type(decoded.get("total_bytes")) is not int
            or not 0 < decoded["total_bytes"] <= policy.max_total_bytes
            or canonical_json(decoded) != stdout
        ):
            raise ClientTransferError("remote response is invalid")
        return decoded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--identity", required=True, type=Path)
    parser.add_argument("--known-hosts", required=True, type=Path)
    parser.add_argument("--host", required=True)
    arguments = parser.parse_args(argv)
    try:
        result = transfer_bundle(
            bundle=arguments.bundle,
            identity=arguments.identity,
            known_hosts=arguments.known_hosts,
            host=arguments.host,
        )
    except ClientTransferError:
        print("error=private corpus transfer failed", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
