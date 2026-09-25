"""Pinned-SSH client for the bounded final-review part-two submission."""

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

from scripts import private_speaker_review_next_final_review_host_contract as host
from scripts import private_speaker_review_next_final_review_submission_contract as contract
from scripts import private_speaker_review_observation_client as base
from scripts.dev_host_contract import validate_host


class NextFinalReviewSubmissionClientError(RuntimeError):
    """Generic local/remote boundary failure."""


def ssh_arguments(*, ssh: str, identity: Path, known_hosts: Path, host_name: str) -> list[str]:
    canonical = validate_host(host_name)
    try:
        arguments = base.ssh_arguments(ssh=ssh, identity=identity, known_hosts=known_hosts, host=canonical)
    except base.SpeakerReviewObservationClientError as error:
        raise NextFinalReviewSubmissionClientError("final-review host policy unavailable") from error
    if not arguments or arguments[-1] != base._host_contract().REVIEW_OBSERVATION_COMMAND:
        raise NextFinalReviewSubmissionClientError("final-review host policy unavailable")
    arguments[-1] = contract.COMMAND
    return arguments


def _drain(stream: IO[bytes]) -> bytes:
    try:
        chunks: list[bytes] = []
        retained = 0
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            if retained <= contract.OUTPUT_MAX_BYTES:
                kept = chunk[: contract.OUTPUT_MAX_BYTES + 1 - retained]
                chunks.append(kept)
                retained += len(kept)
        return b"".join(chunks)
    finally:
        stream.close()


def submit_next_final_review(*, archive_sha256: str, run_id: str, authorization_id: str, maximum_authorized_cost_microusd: int, identity: Path, known_hosts: Path, host_name: str) -> dict[str, object]:
    request = contract.validate_request({"archive_sha256": archive_sha256, "authorization_id": authorization_id, "maximum_authorized_cost_microusd": maximum_authorized_cost_microusd, "operation": contract.OPERATION, "purpose": contract.PURPOSE, "run_id": run_id, "schema_version": contract.PROTOCOL_VERSION, "season_number": contract.SEASON_NUMBER})
    try:
        canonical_host = validate_host(host_name)
        base._regular(identity, private=True)
        base._validate_known_hosts(known_hosts, canonical_host)
    except (ValueError, base.SpeakerReviewObservationClientError) as error:
        raise NextFinalReviewSubmissionClientError("final-review client input invalid") from error
    ssh = shutil.which("ssh")
    if ssh is None:
        raise NextFinalReviewSubmissionClientError("OpenSSH client unavailable")
    try:
        with tempfile.TemporaryDirectory(prefix="cinegraph-next-final-review-") as directory:
            if os.name != "nt":
                Path(directory).chmod(0o700)
            wire = Path(directory) / "request.json"
            wire.write_bytes(contract.canonical_json(request))
            with wire.open("rb") as source:
                process = subprocess.Popen(ssh_arguments(ssh=ssh, identity=identity, known_hosts=known_hosts, host_name=canonical_host), stdin=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
                if process.stdout is None or process.stderr is None:
                    raise OSError
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    stdout_future = pool.submit(_drain, process.stdout)
                    stderr_future = pool.submit(_drain, process.stderr)
                    try:
                        code = process.wait(timeout=host.REVIEW_NEXT_FINAL_REVIEW_TIMEOUT_SECONDS + host.CLIENT_TIMEOUT_MARGIN_SECONDS)
                    except subprocess.TimeoutExpired as error:
                        process.kill()
                        process.wait()
                        raise NextFinalReviewSubmissionClientError("SSH final-review submission failed") from error
                    stdout, stderr = stdout_future.result(timeout=5), stderr_future.result(timeout=5)
    except NextFinalReviewSubmissionClientError:
        raise
    except (OSError, subprocess.SubprocessError, TimeoutError) as error:
        raise NextFinalReviewSubmissionClientError("SSH final-review submission failed") from error
    if code != 0 or stderr or len(stdout) > contract.OUTPUT_MAX_BYTES:
        raise NextFinalReviewSubmissionClientError("remote final-review submission rejected")
    try:
        result = contract.parse_aggregate(stdout)
        if result["run_id"] != request["run_id"]:
            raise ValueError
        return result
    except (TypeError, ValueError) as error:
        raise NextFinalReviewSubmissionClientError("remote final-review response invalid") from error


submit_next_final_review_part = submit_next_final_review


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("archive-sha256", "run-id", "authorization-id", "identity", "known-hosts", "host"):
        parser.add_argument(f"--{name}", required=True, type=Path if name in {"identity", "known-hosts"} else str)
    parser.add_argument("--maximum-authorized-cost-microusd", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        result = submit_next_final_review(archive_sha256=args.archive_sha256, run_id=args.run_id, authorization_id=args.authorization_id, maximum_authorized_cost_microusd=args.maximum_authorized_cost_microusd, identity=args.identity, known_hosts=args.known_hosts, host_name=args.host)
    except (NextFinalReviewSubmissionClientError, ValueError):
        sys.stderr.write('{"error":"speaker_review_next_final_review_rejected","status":"error"}\n')
        return 2
    sys.stdout.buffer.write(contract.canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
