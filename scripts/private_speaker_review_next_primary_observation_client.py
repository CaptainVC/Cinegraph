"""Pinned-SSH client for the separately authorized part-two observer."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

from scripts import private_speaker_review_next_primary_observation_contract as contract
from scripts import private_speaker_review_next_primary_observation_host_contract as host_policy
from scripts import private_speaker_review_observation_client as base
from scripts.dev_host_contract import validate_host


class NextPrimaryObservationClientError(RuntimeError):
    """A deliberately generic local or remote observation failure."""


def ssh_arguments(*, ssh: str, identity: Path, known_hosts: Path, host: str) -> list[str]:
    """Build a shell-free, host-key-pinned invocation for this exact command."""

    if host_policy.REVIEW_NEXT_OBSERVATION_COMMAND != contract.COMMAND:
        raise NextPrimaryObservationClientError("next-primary observation host policy unavailable")
    try:
        arguments = base.ssh_arguments(
            ssh=ssh,
            identity=identity,
            known_hosts=known_hosts,
            host=host,
        )
    except base.SpeakerReviewObservationClientError as error:
        raise NextPrimaryObservationClientError(
            "next-primary observation host policy unavailable"
        ) from error
    if arguments[-1] != base.contract.COMMAND:
        raise NextPrimaryObservationClientError("next-primary observation host policy unavailable")
    arguments[-1] = contract.COMMAND
    return arguments


def observe_next_primary(
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
            "run_id": run_id,
            "authorization_id": authorization_id,
            "maximum_authorized_cost_microusd": maximum_authorized_cost_microusd,
            "operation": contract.OPERATION,
            "purpose": contract.PURPOSE,
            "schema_version": contract.PROTOCOL_VERSION,
            "season_number": contract.SEASON_NUMBER,
        }
    )
    try:
        canonical_host = validate_host(host)
    except ValueError as error:
        raise NextPrimaryObservationClientError("host input is invalid") from error
    try:
        base._regular(identity, private=True)
        base._validate_known_hosts(known_hosts, canonical_host)
    except base.SpeakerReviewObservationClientError as error:
        raise NextPrimaryObservationClientError(
            "next-primary observation input is invalid"
        ) from error
    ssh = shutil.which("ssh")
    if ssh is None:
        raise NextPrimaryObservationClientError("OpenSSH client is unavailable")
    try:
        with tempfile.TemporaryDirectory(prefix="cinegraph-speaker-observe-next-") as directory:
            temp = Path(directory)
            if os.name != "nt":
                temp.chmod(0o700)
            wire = temp / "request.json"
            wire.write_bytes(contract.canonical_json(request))
            try:
                completed = base._run_ssh(
                    ssh_arguments(
                        ssh=ssh,
                        identity=identity,
                        known_hosts=known_hosts,
                        host=canonical_host,
                    ),
                    wire,
                )
            except base.SpeakerReviewObservationClientError as error:
                raise NextPrimaryObservationClientError(
                    "SSH next-primary observation failed"
                ) from error
    except NextPrimaryObservationClientError:
        raise
    except OSError as error:
        raise NextPrimaryObservationClientError("SSH next-primary observation failed") from error
    stdout, stderr = completed.stdout or b"", completed.stderr or b""
    if (
        completed.returncode != 0
        or stderr
        or len(stdout) > contract.OUTPUT_MAX_BYTES
        or len(stderr) > contract.OUTPUT_MAX_BYTES
    ):
        raise NextPrimaryObservationClientError("remote next-primary observation was rejected")
    try:
        return contract.parse_aggregate(stdout)
    except (TypeError, ValueError) as error:
        raise NextPrimaryObservationClientError(
            "remote next-primary observation response is invalid"
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
    args = parser.parse_args(argv)
    try:
        result = observe_next_primary(
            archive_sha256=args.archive_sha256,
            run_id=args.run_id,
            authorization_id=args.authorization_id,
            maximum_authorized_cost_microusd=args.maximum_authorized_cost_microusd,
            identity=args.identity,
            known_hosts=args.known_hosts,
            host=args.host,
        )
    except (NextPrimaryObservationClientError, ValueError):
        sys.stderr.buffer.write(
            b'{"error":"speaker_review_next_primary_observation_rejected","status":"error"}\n'
        )
        return 2
    sys.stdout.buffer.write(contract.canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
