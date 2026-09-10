"""Pinned-SSH client for the v1 next-primary transition."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts import private_speaker_review_next_primary_submission_contract as contract
from scripts import private_speaker_review_submission_client as base


class NextPrimarySubmissionClientError(RuntimeError):
    """Generic local/remote rejection."""


def ssh_arguments(*, ssh: str, identity: Path, known_hosts: Path, host: str) -> list[str]:
    """Build the pinned SSH command while selecting only the next-primary verb."""

    arguments = base.ssh_arguments(ssh=ssh, identity=identity, known_hosts=known_hosts, host=host)
    arguments[-1] = contract.COMMAND
    return arguments


def submit_next_primary(
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
    # Reuse the hardened local file/host validation and subprocess handling,
    # but provide our exact protocol command and parser.
    try:
        canonical_host = base.validate_host(host)
        base._regular(identity, private=True)
        base._validate_known_hosts(known_hosts, canonical_host)
        ssh = base.shutil.which("ssh")
        if ssh is None:
            raise OSError
        import tempfile

        with tempfile.TemporaryDirectory(prefix="cinegraph-speaker-next-") as name:
            wire = Path(name) / "request.json"
            wire.write_bytes(contract.canonical_json(request))
            completed = base._run_ssh(
                ssh_arguments(
                    ssh=ssh, identity=identity, known_hosts=known_hosts, host=canonical_host
                ),
                wire,
            )
    except Exception as error:
        raise NextPrimarySubmissionClientError("remote next-primary submission failed") from error
    stdout, stderr = completed.stdout or b"", completed.stderr or b""
    if completed.returncode or stderr or len(stdout) > contract.OUTPUT_MAX_BYTES:
        raise NextPrimarySubmissionClientError("remote next-primary submission rejected")
    try:
        return contract.parse_aggregate(stdout)
    except (TypeError, ValueError) as error:
        raise NextPrimarySubmissionClientError("remote next-primary response invalid") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("archive-sha256", "run-id", "authorization-id"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--maximum-authorized-cost-microusd", required=True, type=int)
    parser.add_argument("--identity", required=True, type=Path)
    parser.add_argument("--known-hosts", required=True, type=Path)
    parser.add_argument("--host", required=True)
    args = parser.parse_args(argv)
    try:
        result = submit_next_primary(
            archive_sha256=args.archive_sha256,
            run_id=args.run_id,
            authorization_id=args.authorization_id,
            maximum_authorized_cost_microusd=args.maximum_authorized_cost_microusd,
            identity=args.identity,
            known_hosts=args.known_hosts,
            host=args.host,
        )
    except (NextPrimarySubmissionClientError, ValueError):
        sys.stderr.write('{"error":"speaker_review_next_primary_rejected","status":"error"}\n')
        return 2
    sys.stdout.buffer.write(contract.canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
