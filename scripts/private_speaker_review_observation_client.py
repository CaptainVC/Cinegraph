"""Pinned-SSH workstation client for one authorized primary observation."""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import IO

from scripts import private_speaker_review_observation_contract as contract
from scripts.dev_host_contract import (
    known_hosts_line,
    validate_host,
    validate_public_key_line,
)


class SpeakerReviewObservationClientError(RuntimeError):
    """A deliberately generic local or remote observation failure."""


def _host_contract() -> ModuleType:
    """Load host policy lazily so this client remains importable off the VPS."""

    module = import_module("scripts.private_speaker_review_observation_host_contract")
    for name in (
        "REVIEW_USER",
        "REVIEW_OBSERVATION_COMMAND",
        "REVIEW_OBSERVATION_TIMEOUT_SECONDS",
        "REVIEW_OBSERVATION_KILL_AFTER_SECONDS",
        "CLIENT_TIMEOUT_MARGIN_SECONDS",
    ):
        if not hasattr(module, name):
            raise SpeakerReviewObservationClientError(
                "observation host policy unavailable"
            )
    return module


def _is_reparse(result: os.stat_result) -> bool:
    return bool(getattr(result, "st_file_attributes", 0) & 0x400)


def _regular(path: Path, *, private: bool = False) -> os.stat_result:
    try:
        result = path.lstat()
    except OSError as error:
        raise SpeakerReviewObservationClientError(
            "observation input is unavailable"
        ) from error
    if (
        not stat.S_ISREG(result.st_mode)
        or stat.S_ISLNK(result.st_mode)
        or _is_reparse(result)
        or result.st_nlink != 1
        or result.st_size <= 0
        or (private and os.name != "nt" and stat.S_IMODE(result.st_mode) & 0o077)
    ):
        raise SpeakerReviewObservationClientError(
            "observation input is not a safe file"
        )
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
        raise SpeakerReviewObservationClientError(
            "known-hosts input is invalid"
        ) from error


def _ssh_config_path(path: Path) -> str:
    value = path.as_posix()
    if (
        not path.is_absolute()
        or not value
        or any(ord(character) < 32 for character in value)
        or any(character in value for character in ('"', "\\", "%", "$"))
    ):
        raise SpeakerReviewObservationClientError("known-hosts input is invalid")
    return f'"{value}"'


def ssh_arguments(*, ssh: str, identity: Path, known_hosts: Path, host: str) -> list[str]:
    """Build a shell-free, host-key-pinned observation invocation."""

    host_policy = _host_contract()
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
        f"ConnectionAttempts={contract.SSH_CONNECTION_ATTEMPTS}",
        "-o",
        f"ConnectTimeout={contract.SSH_CONNECT_TIMEOUT_SECONDS}",
        "-o",
        f"ServerAliveInterval={contract.SSH_SERVER_ALIVE_INTERVAL_SECONDS}",
        "-o",
        f"ServerAliveCountMax={contract.SSH_SERVER_ALIVE_COUNT_MAX}",
        f"{host_policy.REVIEW_USER}@{host}",
        str(host_policy.REVIEW_OBSERVATION_COMMAND),
    ]


def _read_pipe(stream: IO[bytes]) -> bytes:
    try:
        return stream.read(contract.OUTPUT_MAX_BYTES + 1)
    finally:
        stream.close()


def _run_ssh(arguments: list[str], wire: Path) -> subprocess.CompletedProcess[bytes]:
    process: subprocess.Popen[bytes] | None = None
    host_policy = _host_contract()
    timeout = (
        int(host_policy.REVIEW_OBSERVATION_TIMEOUT_SECONDS)
        + int(host_policy.REVIEW_OBSERVATION_KILL_AFTER_SECONDS)
        + int(host_policy.CLIENT_TIMEOUT_MARGIN_SECONDS)
    )
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
                raise SpeakerReviewObservationClientError("SSH observation failed")
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                stdout_future = executor.submit(_read_pipe, process.stdout)
                stderr_future = executor.submit(_read_pipe, process.stderr)
                try:
                    returncode = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired as error:
                    process.kill()
                    process.wait()
                    raise SpeakerReviewObservationClientError(
                        "SSH observation failed"
                    ) from error
                stdout = stdout_future.result(timeout=5)
                stderr = stderr_future.result(timeout=5)
        return subprocess.CompletedProcess(arguments, returncode, stdout, stderr)
    except SpeakerReviewObservationClientError:
        raise
    except (OSError, subprocess.SubprocessError, TimeoutError) as error:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        raise SpeakerReviewObservationClientError("SSH observation failed") from error


def observe_primary(
    *,
    archive_sha256: str,
    run_id: str,
    authorization_id: str,
    maximum_authorized_cost_microusd: int,
    identity: Path,
    known_hosts: Path,
    host: str,
) -> dict[str, object]:
    """Request one bounded remote observation without transferring artifacts."""

    request = contract.validate_request(
        {
            "archive_sha256": archive_sha256,
            "authorization_id": authorization_id,
            "maximum_authorized_cost_microusd": maximum_authorized_cost_microusd,
            "operation": contract.OPERATION,
            "purpose": contract.PURPOSE,
            "run_id": run_id,
            "schema_version": contract.PROTOCOL_VERSION,
            "season_number": contract.SEASON_NUMBER,
        }
    )
    try:
        canonical_host = validate_host(host)
    except ValueError as error:
        raise SpeakerReviewObservationClientError("host input is invalid") from error
    _regular(identity, private=True)
    _validate_known_hosts(known_hosts, canonical_host)
    ssh = shutil.which("ssh")
    if ssh is None:
        raise SpeakerReviewObservationClientError("OpenSSH client is unavailable")
    with tempfile.TemporaryDirectory(prefix="cinegraph-speaker-observe-") as temp_name:
        temp = Path(temp_name)
        if os.name != "nt":
            temp.chmod(0o700)
        wire = temp / "request.json"
        wire.write_bytes(contract.canonical_json(request))
        completed = _run_ssh(
            ssh_arguments(
                ssh=ssh,
                identity=identity,
                known_hosts=known_hosts,
                host=canonical_host,
            ),
            wire,
        )
    stdout = completed.stdout or b""
    stderr = completed.stderr or b""
    if (
        completed.returncode != 0
        or stderr
        or len(stdout) > contract.OUTPUT_MAX_BYTES
        or len(stderr) > contract.OUTPUT_MAX_BYTES
    ):
        raise SpeakerReviewObservationClientError("remote observation was rejected")
    try:
        return contract.parse_aggregate(stdout)
    except (TypeError, ValueError) as error:
        raise SpeakerReviewObservationClientError(
            "remote response is invalid"
        ) from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--maximum-authorized-cost-microusd", required=True, type=int)
    parser.add_argument("--identity", required=True, type=Path)
    parser.add_argument("--known-hosts", required=True, type=Path)
    parser.add_argument("--host", required=True)
    arguments = parser.parse_args(argv)
    try:
        result = observe_primary(
            archive_sha256=arguments.archive_sha256,
            run_id=arguments.run_id,
            authorization_id=arguments.authorization_id,
            maximum_authorized_cost_microusd=(
                arguments.maximum_authorized_cost_microusd
            ),
            identity=arguments.identity,
            known_hosts=arguments.known_hosts,
            host=arguments.host,
        )
    except (SpeakerReviewObservationClientError, ValueError):
        sys.stderr.buffer.write(
            b'{"error":"speaker_review_observation_rejected","status":"error"}\n'
        )
        return 2
    sys.stdout.buffer.write(contract.canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
