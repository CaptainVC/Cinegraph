"""Pinned-SSH client for observing final-review part one."""

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

from scripts import private_speaker_review_final_review_observation_contract as contract
from scripts import private_speaker_review_final_review_observation_host_contract as host_policy
from scripts import private_speaker_review_observation_client as base
from scripts.dev_host_contract import validate_host


class FinalReviewObservationClientError(RuntimeError):
    """A deliberately generic local or remote observation failure."""


def _read_pipe(stream: IO[bytes]) -> bytes:
    """Drain a child pipe fully while retaining only a bounded prefix."""

    try:
        chunks: list[bytes] = []
        retained = 0
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            if retained <= contract.OUTPUT_MAX_BYTES:
                keep = chunk[: contract.OUTPUT_MAX_BYTES + 1 - retained]
                chunks.append(keep)
                retained += len(keep)
        return b"".join(chunks)
    finally:
        stream.close()


def _run_ssh(arguments: list[str], wire: Path) -> subprocess.CompletedProcess[bytes]:
    process: subprocess.Popen[bytes] | None = None
    timeout = (
        host_policy.REVIEW_FINAL_REVIEW_OBSERVATION_TIMEOUT_SECONDS
        + host_policy.REVIEW_FINAL_REVIEW_OBSERVATION_KILL_AFTER_SECONDS
        + host_policy.CLIENT_TIMEOUT_MARGIN_SECONDS
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
                raise OSError
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                stdout_future = executor.submit(_read_pipe, process.stdout)
                stderr_future = executor.submit(_read_pipe, process.stderr)
                try:
                    returncode = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired as error:
                    process.kill()
                    process.wait()
                    stdout_future.result(timeout=5)
                    stderr_future.result(timeout=5)
                    raise FinalReviewObservationClientError(
                        "SSH final-review observation failed"
                    ) from error
                stdout = stdout_future.result(timeout=5)
                stderr = stderr_future.result(timeout=5)
        return subprocess.CompletedProcess(arguments, returncode, stdout, stderr)
    except FinalReviewObservationClientError:
        raise
    except (OSError, subprocess.SubprocessError, TimeoutError) as error:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        raise FinalReviewObservationClientError("SSH final-review observation failed") from error


def ssh_arguments(*, ssh: str, identity: Path, known_hosts: Path, host: str) -> list[str]:
    if host_policy.REVIEW_FINAL_REVIEW_OBSERVATION_COMMAND != contract.COMMAND:
        raise FinalReviewObservationClientError("final-review observation host policy unavailable")
    try:
        arguments = base.ssh_arguments(
            ssh=ssh, identity=identity, known_hosts=known_hosts, host=host
        )
    except base.SpeakerReviewObservationClientError as error:
        raise FinalReviewObservationClientError(
            "final-review observation host policy unavailable"
        ) from error
    if not arguments or arguments[-1] != base.contract.COMMAND:
        raise FinalReviewObservationClientError("final-review observation host policy unavailable")
    arguments[-1] = contract.COMMAND
    return arguments


def observe_final_review_part_one(
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
        raise FinalReviewObservationClientError("final-review observation input invalid") from error
    ssh = shutil.which("ssh")
    if ssh is None:
        raise FinalReviewObservationClientError("OpenSSH client unavailable")
    try:
        with tempfile.TemporaryDirectory(prefix="cinegraph-final-review-observe-") as directory:
            temp = Path(directory)
            if os.name != "nt":
                temp.chmod(0o700)
            wire = temp / "request.json"
            wire.write_bytes(contract.canonical_json(request))
            completed = _run_ssh(
                ssh_arguments(
                    ssh=ssh, identity=identity, known_hosts=known_hosts, host=canonical_host
                ),
                wire,
            )
    except base.SpeakerReviewObservationClientError as error:
        raise FinalReviewObservationClientError("SSH final-review observation failed") from error
    except OSError as error:
        raise FinalReviewObservationClientError("SSH final-review observation failed") from error
    stdout, stderr = completed.stdout or b"", completed.stderr or b""
    if (
        completed.returncode != 0
        or stderr
        or len(stdout) > contract.OUTPUT_MAX_BYTES
        or len(stderr) > contract.OUTPUT_MAX_BYTES
    ):
        raise FinalReviewObservationClientError("remote final-review observation rejected")
    try:
        result = contract.parse_aggregate(stdout)
        if result["run_id"] != request["run_id"]:
            raise ValueError("response binding mismatch")
        ceiling = request["maximum_authorized_cost_microusd"]
        if result["maximum_authorized_cost_microusd"] != ceiling:
            raise ValueError("authorization mismatch")
        if (
            result["actual_primary_cost_microusd"]
            + result["actual_adjudication_cost_microusd"]
            + result["estimated_final_review_cost_microusd"]
            > ceiling
        ):
            raise ValueError("cost ceiling exceeded")
        return result
    except (TypeError, ValueError) as error:
        raise FinalReviewObservationClientError(
            "remote final-review observation response invalid"
        ) from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("archive-sha256", "run-id", "authorization-id", "identity", "known-hosts", "host"):
        parser.add_argument(
            f"--{name}", required=True, type=Path if name in {"identity", "known-hosts"} else str
        )
    parser.add_argument("--maximum-authorized-cost-microusd", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        result = observe_final_review_part_one(
            archive_sha256=args.archive_sha256,
            run_id=args.run_id,
            authorization_id=args.authorization_id,
            maximum_authorized_cost_microusd=args.maximum_authorized_cost_microusd,
            identity=args.identity,
            known_hosts=args.known_hosts,
            host=args.host,
        )
    except (FinalReviewObservationClientError, ValueError):
        sys.stderr.write(
            '{"error":"speaker_review_final_review_observation_rejected","status":"error"}\n'
        )
        return 2
    sys.stdout.buffer.write(contract.canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
