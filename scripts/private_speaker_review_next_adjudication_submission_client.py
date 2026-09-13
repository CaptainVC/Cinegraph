"""Pinned-SSH client for one subsequent adjudication-part submission."""

from __future__ import annotations

import argparse
import concurrent.futures
import shutil
import subprocess
import sys
import tempfile
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import IO

from scripts import private_speaker_review_submission_client as base


class NextAdjudicationSubmissionClientError(RuntimeError):
    """A deliberately generic local or remote rejection."""


def _contract() -> ModuleType:
    try:
        module = import_module(
            "scripts.private_speaker_review_next_adjudication_submission_contract"
        )
        for name in (
            "COMMAND",
            "OPERATION",
            "PROTOCOL_VERSION",
            "PURPOSE",
            "SEASON_NUMBER",
            "REQUEST_MAX_BYTES",
            "OUTPUT_MAX_BYTES",
            "validate_request",
            "parse_aggregate",
            "canonical_json",
        ):
            if not hasattr(module, name):
                raise ImportError
        return module
    except Exception as error:
        raise NextAdjudicationSubmissionClientError("submission contract unavailable") from error


def ssh_arguments(*, ssh: str, identity: Path, known_hosts: Path, host: str) -> list[str]:
    policy = import_module("scripts.private_speaker_review_next_adjudication_host_contract")
    if policy.REVIEW_NEXT_ADJUDICATION_COMMAND != _contract().COMMAND:
        raise NextAdjudicationSubmissionClientError("submission host policy unavailable")
    arguments = base.ssh_arguments(ssh=ssh, identity=identity, known_hosts=known_hosts, host=host)
    arguments[-1] = str(_contract().COMMAND)
    return arguments


def _read_pipe(stream: IO[bytes], maximum: int) -> bytes:
    try:
        return stream.read(maximum + 1)
    finally:
        stream.close()


def _run_ssh(arguments: list[str], wire: Path) -> subprocess.CompletedProcess[bytes]:
    policy = import_module("scripts.private_speaker_review_next_adjudication_host_contract")
    timeout = (
        int(policy.REVIEW_NEXT_ADJUDICATION_TIMEOUT_SECONDS)
        + int(policy.REVIEW_NEXT_ADJUDICATION_KILL_AFTER_SECONDS)
        + int(policy.CLIENT_TIMEOUT_MARGIN_SECONDS)
    )
    maximum = int(_contract().OUTPUT_MAX_BYTES)
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
                raise OSError
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                out = executor.submit(_read_pipe, process.stdout, maximum)
                err = executor.submit(_read_pipe, process.stderr, maximum)
                try:
                    code = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired as error:
                    process.kill()
                    process.wait()
                    raise NextAdjudicationSubmissionClientError("SSH submission failed") from error
                stdout = out.result(timeout=5)
                stderr = err.result(timeout=5)
        return subprocess.CompletedProcess(arguments, code, stdout, stderr)
    except NextAdjudicationSubmissionClientError:
        raise
    except (OSError, subprocess.SubprocessError, TimeoutError) as error:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        raise NextAdjudicationSubmissionClientError("SSH submission failed") from error


def submit_next_adjudication(
    *,
    archive_sha256: str,
    run_id: str,
    authorization_id: str,
    maximum_authorized_cost_microusd: int,
    identity: Path,
    known_hosts: Path,
    host: str,
) -> dict[str, object]:
    contract = _contract()
    try:
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
        canonical_host = base.validate_host(host)
        base._regular(identity, private=True)
        base._validate_known_hosts(known_hosts, canonical_host)
        ssh = shutil.which("ssh")
        if ssh is None:
            raise OSError
        with tempfile.TemporaryDirectory(prefix="cinegraph-speaker-next-adjudication-") as name:
            wire = Path(name) / "request.json"
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
    except NextAdjudicationSubmissionClientError:
        raise
    except Exception as error:
        raise NextAdjudicationSubmissionClientError(
            "remote next-adjudication submission failed"
        ) from error
    stdout, stderr = completed.stdout or b"", completed.stderr or b""
    if (
        completed.returncode != 0
        or stderr
        or len(stdout) > int(contract.OUTPUT_MAX_BYTES)
        or len(stderr) > int(contract.OUTPUT_MAX_BYTES)
    ):
        raise NextAdjudicationSubmissionClientError(
            "remote next-adjudication submission rejected"
        )
    try:
        result = contract.parse_aggregate(stdout)
        if (
            result["run_id"] != request["run_id"]
            or result["actual_primary_cost_microusd"]
            + result["estimated_adjudication_cost_microusd"]
            > request["maximum_authorized_cost_microusd"]
        ):
            raise ValueError("response binding mismatch")
        return result
    except (TypeError, ValueError) as error:
        raise NextAdjudicationSubmissionClientError(
            "remote next-adjudication response invalid"
        ) from error


# The explicit part alias mirrors the LangGraph operation name.
submit_next_adjudication_part = submit_next_adjudication


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
        result = submit_next_adjudication(
            archive_sha256=args.archive_sha256,
            run_id=args.run_id,
            authorization_id=args.authorization_id,
            maximum_authorized_cost_microusd=args.maximum_authorized_cost_microusd,
            identity=args.identity,
            known_hosts=args.known_hosts,
            host=args.host,
        )
    except (NextAdjudicationSubmissionClientError, ValueError):
        sys.stderr.write(
            '{"error":"speaker_review_next_adjudication_rejected","status":"error"}\n'
        )
        return 2
    sys.stdout.buffer.write(_contract().canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
