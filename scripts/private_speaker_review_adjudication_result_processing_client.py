"""Pinned-SSH client for provider-free adjudication-result processing."""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import IO

from scripts import private_speaker_review_adjudication_result_processing_contract as contract
from scripts import (
    private_speaker_review_adjudication_result_processing_host_contract as host_policy,
)
from scripts import private_speaker_review_next_primary_observation_client as observation
from scripts import private_speaker_review_observation_client as base
from scripts.dev_host_contract import validate_host


class AdjudicationResultProcessingClientError(RuntimeError):
    """Generic client failure that never carries private evidence."""


def ssh_arguments(*, ssh: str, identity: Path, known_hosts: Path, host: str) -> list[str]:
    if host_policy.REVIEW_ADJUDICATION_RESULT_PROCESSING_COMMAND != contract.COMMAND:
        raise AdjudicationResultProcessingClientError("adjudication host policy unavailable")
    try:
        args = observation.ssh_arguments(
            ssh=ssh, identity=identity, known_hosts=known_hosts, host=host
        )
    except (
        base.SpeakerReviewObservationClientError,
        observation.NextPrimaryObservationClientError,
    ) as error:
        raise AdjudicationResultProcessingClientError(
            "adjudication host policy unavailable"
        ) from error
    if not args or args[-1] != observation.contract.COMMAND:
        raise AdjudicationResultProcessingClientError("adjudication host policy unavailable")
    args[-1] = contract.COMMAND
    return args


def _read_pipe(stream: IO[bytes]) -> bytes:
    try:
        return stream.read(contract.OUTPUT_MAX_BYTES + 1)
    finally:
        stream.close()


def _run_ssh(arguments: list[str], wire: Path) -> subprocess.CompletedProcess[bytes]:
    process: subprocess.Popen[bytes] | None = None
    timeout = (
        host_policy.REVIEW_ADJUDICATION_RESULT_PROCESSING_TIMEOUT_SECONDS
        + host_policy.REVIEW_ADJUDICATION_RESULT_PROCESSING_KILL_AFTER_SECONDS
        + host_policy.CLIENT_TIMEOUT_MARGIN_SECONDS
    )
    try:
        with wire.open("rb") as source:
            process = subprocess.Popen(
                arguments, stdin=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False
            )
            if process.stdout is None or process.stderr is None:
                raise AdjudicationResultProcessingClientError("SSH adjudication processing failed")
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                out = executor.submit(_read_pipe, process.stdout)
                err = executor.submit(_read_pipe, process.stderr)
                try:
                    code = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired as error:
                    process.kill()
                    process.wait()
                    raise AdjudicationResultProcessingClientError(
                        "SSH adjudication processing failed"
                    ) from error
                stdout, stderr = out.result(timeout=5), err.result(timeout=5)
        return subprocess.CompletedProcess(arguments, code, stdout, stderr)
    except AdjudicationResultProcessingClientError:
        raise
    except (OSError, subprocess.SubprocessError, TimeoutError) as error:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        raise AdjudicationResultProcessingClientError(
            "SSH adjudication processing failed"
        ) from error


def process_adjudication_results(
    *,
    archive_sha256: str,
    run_id: str,
    authorization_id: str,
    maximum_authorized_cost_microusd: int,
    identity: Path,
    known_hosts: Path,
    host: str,
) -> dict[str, object]:
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
        base._regular(identity, private=True)
        base._validate_known_hosts(known_hosts, canonical_host)
    except (ValueError, base.SpeakerReviewObservationClientError) as error:
        raise AdjudicationResultProcessingClientError(
            "adjudication processing input is invalid"
        ) from error
    ssh = shutil.which("ssh")
    if ssh is None:
        raise AdjudicationResultProcessingClientError("OpenSSH client is unavailable")
    try:
        with tempfile.TemporaryDirectory(prefix="cinegraph-speaker-process-adjudication-") as name:
            temporary_directory = Path(name)
            if os.name != "nt":
                temporary_directory.chmod(0o700)
            wire = temporary_directory / "request.json"
            wire.write_bytes(contract.canonical_json(request))
            completed = _run_ssh(
                ssh_arguments(
                    ssh=ssh, identity=identity, known_hosts=known_hosts, host=canonical_host
                ),
                wire,
            )
    except AdjudicationResultProcessingClientError:
        raise
    except OSError as error:
        raise AdjudicationResultProcessingClientError(
            "SSH adjudication processing failed"
        ) from error
    stdout, stderr = completed.stdout or b"", completed.stderr or b""
    if completed.returncode != 0 or stderr or len(stdout) > contract.OUTPUT_MAX_BYTES:
        raise AdjudicationResultProcessingClientError("remote adjudication processing was rejected")
    try:
        return contract.parse_aggregate(stdout)
    except (TypeError, ValueError) as error:
        raise AdjudicationResultProcessingClientError(
            "remote adjudication response is invalid"
        ) from error


process_adjudication_result_processing = process_adjudication_results
process_results = process_adjudication_results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("archive-sha256", "run-id", "authorization-id", "identity", "known-hosts", "host"):
        parser.add_argument(
            f"--{name}", required=True, type=Path if name in {"identity", "known-hosts"} else str
        )
    parser.add_argument("--maximum-authorized-cost-microusd", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        result = process_adjudication_results(
            archive_sha256=args.archive_sha256,
            run_id=args.run_id,
            authorization_id=args.authorization_id,
            maximum_authorized_cost_microusd=args.maximum_authorized_cost_microusd,
            identity=args.identity,
            known_hosts=args.known_hosts,
            host=args.host,
        )
    except (AdjudicationResultProcessingClientError, ValueError):
        sys.stderr.write(
            '{"error":"speaker_review_adjudication_result_processing_rejected","status":"error"}\n'
        )
        return 2
    sys.stdout.buffer.write(contract.canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
