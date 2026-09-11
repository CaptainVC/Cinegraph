"""Pinned-SSH client for provider-free primary-result processing."""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import IO

from scripts import (
    private_speaker_review_next_primary_observation_client as observation_client,
)
from scripts import (
    private_speaker_review_observation_client as base,
)
from scripts import (
    private_speaker_review_primary_result_processing_contract as contract,
)
from scripts import (
    private_speaker_review_primary_result_processing_host_contract as host_policy,
)
from scripts.dev_host_contract import validate_host


class PrimaryResultProcessingClientError(RuntimeError):
    """A deliberately generic local or remote processing failure."""


_BASE_RUNNER = base._run_ssh


def _host_contract() -> ModuleType:
    """Load and minimally verify policy before constructing SSH arguments."""

    module = host_policy
    for name in (
        "REVIEW_USER",
        "REVIEW_PRIMARY_RESULT_PROCESSING_COMMAND",
        "REVIEW_PRIMARY_RESULT_PROCESSING_TIMEOUT_SECONDS",
        "REVIEW_PRIMARY_RESULT_PROCESSING_KILL_AFTER_SECONDS",
        "CLIENT_TIMEOUT_MARGIN_SECONDS",
    ):
        if not hasattr(module, name):
            raise PrimaryResultProcessingClientError(
                "primary-result processing host policy unavailable"
            )
    return module


def ssh_arguments(*, ssh: str, identity: Path, known_hosts: Path, host: str) -> list[str]:
    """Build one shell-free, host-key-pinned invocation of this exact command."""

    policy = _host_contract()
    if policy.REVIEW_PRIMARY_RESULT_PROCESSING_COMMAND != contract.COMMAND:
        raise PrimaryResultProcessingClientError(
            "primary-result processing host policy unavailable"
        )
    try:
        arguments = observation_client.ssh_arguments(
            ssh=ssh, identity=identity, known_hosts=known_hosts, host=host
        )
    except observation_client.NextPrimaryObservationClientError as error:
        raise PrimaryResultProcessingClientError(
            "primary-result processing host policy unavailable"
        ) from error
    arguments[-1] = contract.COMMAND
    return arguments


def _read_pipe(stream: IO[bytes]) -> bytes:
    try:
        return stream.read(contract.OUTPUT_MAX_BYTES + 1)
    finally:
        stream.close()


def _run_ssh(arguments: list[str], wire: Path) -> subprocess.CompletedProcess[bytes]:
    """Run SSH without a shell and bound both output streams and wall time."""

    process: subprocess.Popen[bytes] | None = None
    policy = _host_contract()
    timeout = (
        int(policy.REVIEW_PRIMARY_RESULT_PROCESSING_TIMEOUT_SECONDS)
        + int(policy.REVIEW_PRIMARY_RESULT_PROCESSING_KILL_AFTER_SECONDS)
        + int(policy.CLIENT_TIMEOUT_MARGIN_SECONDS)
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
                raise PrimaryResultProcessingClientError(
                    "SSH primary-result processing failed"
                )
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                stdout_future = executor.submit(_read_pipe, process.stdout)
                stderr_future = executor.submit(_read_pipe, process.stderr)
                try:
                    returncode = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired as error:
                    process.kill()
                    process.wait()
                    raise PrimaryResultProcessingClientError(
                        "SSH primary-result processing failed"
                    ) from error
                stdout = stdout_future.result(timeout=5)
                stderr = stderr_future.result(timeout=5)
        return subprocess.CompletedProcess(arguments, returncode, stdout, stderr)
    except PrimaryResultProcessingClientError:
        raise
    except (OSError, subprocess.SubprocessError, TimeoutError) as error:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        raise PrimaryResultProcessingClientError(
            "SSH primary-result processing failed"
        ) from error


def process_primary_results(
    *,
    archive_sha256: str,
    run_id: str,
    authorization_id: str,
    maximum_authorized_cost_microusd: int,
    identity: Path,
    known_hosts: Path,
    host: str,
) -> dict[str, object]:
    """Request one provider-free processing transition and return its aggregate."""

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
        raise PrimaryResultProcessingClientError("host input is invalid") from error
    try:
        base._regular(identity, private=True)
        base._validate_known_hosts(known_hosts, canonical_host)
    except base.SpeakerReviewObservationClientError as error:
        raise PrimaryResultProcessingClientError(
            "primary-result processing input is invalid"
        ) from error
    ssh = shutil.which("ssh")
    if ssh is None:
        raise PrimaryResultProcessingClientError("OpenSSH client is unavailable")
    try:
        with tempfile.TemporaryDirectory(prefix="cinegraph-speaker-process-primary-") as name:
            temp = Path(name)
            if os.name != "nt":
                temp.chmod(0o700)
            wire = temp / "request.json"
            wire.write_bytes(contract.canonical_json(request))
            arguments = ssh_arguments(
                ssh=ssh,
                identity=identity,
                known_hosts=known_hosts,
                host=canonical_host,
            )
            # Reuse a caller-provided patched base runner (as the sibling
            # transition clients do) while keeping this boundary's own
            # centrally bounded runner in normal operation.
            runner = _run_ssh if base._run_ssh is _BASE_RUNNER else base._run_ssh
            try:
                completed = runner(arguments, wire)
            except base.SpeakerReviewObservationClientError as error:
                raise PrimaryResultProcessingClientError(
                    "SSH primary-result processing failed"
                ) from error
    except PrimaryResultProcessingClientError:
        raise
    except OSError as error:
        raise PrimaryResultProcessingClientError(
            "SSH primary-result processing failed"
        ) from error
    stdout, stderr = completed.stdout or b"", completed.stderr or b""
    if (
        completed.returncode != 0
        or stderr
        or len(stdout) > contract.OUTPUT_MAX_BYTES
        or len(stderr) > contract.OUTPUT_MAX_BYTES
    ):
        raise PrimaryResultProcessingClientError(
            "remote primary-result processing was rejected"
        )
    try:
        return contract.parse_aggregate(stdout)
    except (TypeError, ValueError) as error:
        raise PrimaryResultProcessingClientError(
            "remote primary-result processing response is invalid"
        ) from error


# Keep the operation spelling discoverable to callers that use the transition
# name rather than the worker function name.
process_primary_result_processing = process_primary_results
process_results = process_primary_results
SpeakerReviewPrimaryResultProcessingClientError = PrimaryResultProcessingClientError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--maximum-authorized-cost-microusd", required=True, type=int)
    parser.add_argument("--identity", required=True, type=Path)
    parser.add_argument("--known-hosts", required=True, type=Path)
    parser.add_argument("--host", required=True)
    args = parser.parse_args(argv)
    try:
        result = process_primary_results(
            archive_sha256=args.archive_sha256,
            run_id=args.run_id,
            authorization_id=args.authorization_id,
            maximum_authorized_cost_microusd=args.maximum_authorized_cost_microusd,
            identity=args.identity,
            known_hosts=args.known_hosts,
            host=args.host,
        )
    except (PrimaryResultProcessingClientError, ValueError):
        sys.stderr.buffer.write(
            b'{"error":"speaker_review_primary_result_processing_rejected","status":"error"}\n'
        )
        return 2
    sys.stdout.buffer.write(contract.canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
