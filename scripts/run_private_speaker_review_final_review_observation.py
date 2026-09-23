"""Root-only coordinator for one final-review part-one observation."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from pathlib import Path
from typing import BinaryIO, Mapping

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT, ROOT / "scripts", ROOT / "src"):
    if os.fspath(_path) not in sys.path:
        sys.path.insert(0, os.fspath(_path))

# ruff: noqa: E402
from scripts import (
    private_speaker_review_final_review_observation_contract as contract,
)
from scripts import (
    private_speaker_review_final_review_observation_host_contract as host,
)
from scripts import run_private_speaker_review_final_review as phase79


class FinalReviewObservationError(RuntimeError):
    """Generic rejection that never includes private review evidence."""


MAX_RECORD_BYTES = 128 * 1024
MAX_TOTAL_BYTES = phase79.MAX_TOTAL_BYTES
STATE_NAME = phase79.STATE_NAME
ROOT_OWNER = (0, 0)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def _stable(path: Path, *, maximum: int, mode: int, owner: tuple[int, int]) -> bytes:
    return phase79._stable(path, maximum=maximum, mode=mode, owner=owner)


def _record(path: Path) -> tuple[dict[str, object], str]:
    raw = _stable(path, maximum=MAX_RECORD_BYTES, mode=0o600, owner=ROOT_OWNER)
    return phase79._decode(raw), _sha(raw)


def _read_request(stream: BinaryIO) -> dict[str, object]:
    raw = stream.read(contract.REQUEST_MAX_BYTES + 1)
    if len(raw) > contract.REQUEST_MAX_BYTES:
        raise FinalReviewObservationError("request too large")
    try:
        return contract.parse_request(raw)
    except (TypeError, ValueError) as error:
        raise FinalReviewObservationError("request invalid") from error


def _phase79_request(request: Mapping[str, object]) -> dict[str, object]:
    return {
        "archive_sha256": request["archive_sha256"],
        "authorization_id": request["authorization_id"],
        "maximum_authorized_cost_microusd": request["maximum_authorized_cost_microusd"],
        "operation": phase79.contract.OPERATION,
        "purpose": phase79.contract.PURPOSE,
        "run_id": request["run_id"],
        "schema_version": phase79.contract.PROTOCOL_VERSION,
        "season_number": phase79.contract.SEASON_NUMBER,
    }


def _submission_chain(
    request: Mapping[str, object],
) -> tuple[Path, dict[str, bytes], object, dict[str, object], str, str]:
    run = phase79._run_directory(request)
    intent_path = (
        host.REVIEW_FINAL_REVIEW_SUBMISSION_RECEIPTS_ROOT / f"{request['run_id']}.intent.json"
    )
    receipt_path = host.REVIEW_FINAL_REVIEW_SUBMISSION_RECEIPTS_ROOT / f"{request['run_id']}.json"
    if not intent_path.exists() or not receipt_path.exists():
        raise FinalReviewObservationError("Phase79 receipt chain missing")
    intent, intent_sha = _record(intent_path)
    receipt, receipt_sha = _record(receipt_path)
    if receipt.get("intent_sha256") != intent_sha or receipt.get("status") != "receipt":
        raise FinalReviewObservationError("Phase79 receipt chain invalid")
    if (
        intent.get("run_id") != request["run_id"]
        or intent.get("archive_sha256") != request["archive_sha256"]
        or intent.get("authorization_id") != request["authorization_id"]
        or intent.get("operation") != phase79.contract.OPERATION
        or receipt.get("result", {}).get("run_status") != "final_review_submitted"
    ):
        raise FinalReviewObservationError("Phase79 receipt binding invalid")
    result = phase79.process_request(_phase79_request(request))
    if (
        result.get("status") != "already_submitted"
        or result.get("run_status") != "final_review_submitted"
    ):
        raise FinalReviewObservationError("Phase79 checkpoint invalid")
    contents, state = phase79._inventory(run)
    if (
        phase79._status_value(state) != "final_review_submitted"
        or state.final_review_completed_part_count != 0
    ):
        raise FinalReviewObservationError("submitted checkpoint invalid")
    estimated = phase79._estimate_final_review_cost(contents, state)
    if estimated != intent.get("estimated_final_review_cost_microusd"):
        raise FinalReviewObservationError("Phase79 estimate changed")
    phase79._validate_budget(_phase79_request(request), state, estimated)
    if phase79._validate_application_journals(
        contents, _phase79_request(request), state, intent.get("part_one_request_sha256")
    ) != (True, True):
        raise FinalReviewObservationError("Phase79 application journals invalid")
    # The provider-free Phase79 replay above independently checks its authorization
    # claim and the full Phase78 predecessor chain before this boundary can run.
    return run, contents, state, intent, receipt_sha, intent_sha


def _snapshot(
    run: Path, *, owner: tuple[int, int], names: set[str] | None = None
) -> dict[str, bytes]:
    try:
        actual = {entry.name for entry in run.iterdir()}
        if names is not None and actual != names:
            raise OSError
        if not actual or any(name in {".", ".."} or "/" in name or "\\" in name for name in actual):
            raise OSError
        contents: dict[str, bytes] = {}
        total = 0
        for name in sorted(actual):
            raw = _stable(
                run / name,
                maximum=64 * 1024 if name == STATE_NAME else phase79.MAX_FILE_BYTES,
                mode=0o600,
                owner=owner,
            )
            total += len(raw)
            if total > MAX_TOTAL_BYTES:
                raise OSError
            contents[name] = raw
        return contents
    except OSError as error:
        raise FinalReviewObservationError("run inventory invalid") from error


def _state_document(raw: bytes) -> dict[str, object]:
    try:
        value = phase79._decode_document(raw)
    except (TypeError, ValueError) as error:
        raise FinalReviewObservationError("run state invalid") from error
    if set(value) != phase79.STATE_KEYS:
        raise FinalReviewObservationError("run state invalid")
    return value


def _hashes(contents: Mapping[str, bytes]) -> dict[str, str]:
    return {name: _sha(raw) for name, raw in sorted(contents.items())}


def _validate_inventory_transition(
    before: Mapping[str, bytes], after: Mapping[str, bytes], *, allowed_additions: set[str]
) -> None:
    removed = set(before) - set(after)
    added = set(after) - set(before)
    if (
        removed
        or not added <= allowed_additions
        or any(after[name] != raw for name, raw in before.items() if name != STATE_NAME)
    ):
        raise FinalReviewObservationError("observation changed unexpected files")


def _aggregate(
    request: Mapping[str, object],
    state: Mapping[str, object],
    *,
    status: str,
    estimated: int,
    state_maximum: int,
) -> dict[str, object]:
    try:
        value = {
            "actual_adjudication_cost_microusd": phase79._cost_micros(
                state["actual_adjudication_cost_usd"]
            ),
            "actual_final_review_cost_microusd": phase79._cost_micros(
                state["actual_final_review_cost_usd"]
            ),
            "actual_primary_cost_microusd": phase79._cost_micros(state["actual_primary_cost_usd"]),
            "estimated_final_review_cost_microusd": estimated,
            "final_review_completed_part_count": state["final_review_completed_part_count"],
            "final_review_part_count": state["final_review_part_count"],
            "maximum_authorized_cost_microusd": request["maximum_authorized_cost_microusd"],
            "operation": contract.OPERATION,
            "purpose": contract.PURPOSE,
            "run_id": request["run_id"],
            "run_status": state["status"],
            "season_number": contract.SEASON_NUMBER,
            "state_maximum_cost_microusd": state_maximum,
            "status": status,
        }
        return contract.validate_aggregate(value, status=status)
    except (KeyError, TypeError, ValueError) as error:
        raise FinalReviewObservationError("aggregate invalid") from error


def _phase80_bindings(
    request: Mapping[str, object],
    contents: Mapping[str, bytes],
    *,
    receipt_sha: str,
    estimated: int,
) -> dict[str, str]:
    digests = phase79._digests(contents)
    return {
        contract.ENV_EXPECTED_SUBMISSION_RECEIPT_SHA256: receipt_sha,
        contract.ENV_EXPECTED_ESTIMATED_FINAL_REVIEW_COST_MICROUSD: str(estimated),
        contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: digests["state"],
        contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: digests["artifacts"],
        contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: digests["journals"],
        contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256: digests["outputs"],
        contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256: digests["derived"],
        contract.ENV_EXPECTED_REQUEST_SHA256: _sha(
            contents["final-review-part-0001-requests.jsonl"]
        ),
    }


def _worker_once(
    request: Mapping[str, object], run: Path, bindings: Mapping[str, str]
) -> dict[str, object]:
    # Reuse the Phase79 coordinator's audited bounded I/O, exact Compose
    # identity inspection, narrow run mount, timeout, and cleanup machinery.
    fields = {
        "REVIEW_FINAL_REVIEW_COMPOSE_PROFILE": host.REVIEW_FINAL_REVIEW_OBSERVATION_COMPOSE_PROFILE,
        "REVIEW_FINAL_REVIEW_COMPOSE_SERVICE": host.REVIEW_FINAL_REVIEW_OBSERVATION_COMPOSE_SERVICE,
        "REVIEW_FINAL_REVIEW_COMPOSE_PROJECT": host.REVIEW_FINAL_REVIEW_OBSERVATION_COMPOSE_PROJECT,
        "REVIEW_FINAL_REVIEW_CONTAINER_NAME": host.REVIEW_FINAL_REVIEW_OBSERVATION_CONTAINER_NAME,
        "REVIEW_FINAL_REVIEW_CONTAINER_WORKDIR": host.REVIEW_FINAL_REVIEW_OBSERVATION_CONTAINER_WORKDIR,
        "REVIEW_FINAL_REVIEW_CONTAINER_COMMAND": host.REVIEW_FINAL_REVIEW_OBSERVATION_CONTAINER_COMMAND,
        "REVIEW_FINAL_REVIEW_NETWORK": host.REVIEW_FINAL_REVIEW_OBSERVATION_NETWORK,
        "REVIEW_FINAL_REVIEW_TIMEOUT_SECONDS": host.REVIEW_FINAL_REVIEW_OBSERVATION_TIMEOUT_SECONDS,
        "REVIEW_FINAL_REVIEW_KILL_AFTER_SECONDS": host.REVIEW_FINAL_REVIEW_OBSERVATION_KILL_AFTER_SECONDS,
        "REVIEW_FINAL_REVIEW_WORKER_UID": host.REVIEW_FINAL_REVIEW_OBSERVATION_WORKER_UID,
        "REVIEW_FINAL_REVIEW_WORKER_GID": host.REVIEW_FINAL_REVIEW_OBSERVATION_WORKER_GID,
        "REVIEW_FINAL_REVIEW_RUNS_TARGET": host.REVIEW_FINAL_REVIEW_OBSERVATION_RUNS_TARGET,
        "REVIEW_FINAL_REVIEW_SECRET_TARGET": host.REVIEW_FINAL_REVIEW_OBSERVATION_SECRET_TARGET,
        "REVIEW_FINAL_REVIEW_TMP_TARGET": host.REVIEW_FINAL_REVIEW_OBSERVATION_TMP_TARGET,
    }
    old = {name: getattr(phase79.host, name) for name in fields}
    old_parser = phase79.contract.parse_aggregate
    try:
        for name, value in fields.items():
            setattr(phase79.host, name, value)
        phase79.contract.parse_aggregate = contract.parse_aggregate
        return phase79._run_worker(request, run, bindings)
    except phase79.FinalReviewSubmissionError as error:
        raise FinalReviewObservationError("observation worker failed") from error
    finally:
        for name, value in old.items():
            setattr(phase79.host, name, value)
        phase79.contract.parse_aggregate = old_parser


def _write_record(path: Path, value: Mapping[str, object]) -> str:
    return phase79._write_once(path, value)


def _replay(
    request: Mapping[str, object], intent_path: Path, receipt_path: Path
) -> dict[str, object] | None:
    if not receipt_path.exists():
        if intent_path.exists():
            intent, _ = _record(intent_path)
            phase79_intent, phase79_intent_sha = _record(
                host.REVIEW_FINAL_REVIEW_SUBMISSION_RECEIPTS_ROOT
                / f"{request['run_id']}.intent.json"
            )
            _, phase79_receipt_sha = _record(
                host.REVIEW_FINAL_REVIEW_SUBMISSION_RECEIPTS_ROOT / f"{request['run_id']}.json"
            )
            run = phase79._run_directory(request)
            owner = (
                host.REVIEW_FINAL_REVIEW_OBSERVATION_WORKER_UID,
                host.REVIEW_FINAL_REVIEW_OBSERVATION_WORKER_GID,
            )
            current = _snapshot(run, owner=owner)
            if (
                intent.get("request_sha256") != _sha(_canonical(request))
                or intent.get("submission_intent_sha256") != phase79_intent_sha
                or intent.get("submission_receipt_sha256") != phase79_receipt_sha
                or intent.get("status") != "intent"
                or phase79_intent.get("run_id") != request["run_id"]
                or _hashes(current) != intent.get("pre_hashes")
            ):
                raise FinalReviewObservationError("observation reconciliation required")
            return None
        return None
    receipt, _ = _record(receipt_path)
    intent, intent_sha = _record(intent_path)
    phase79_intent, phase79_intent_sha = _record(
        host.REVIEW_FINAL_REVIEW_SUBMISSION_RECEIPTS_ROOT / f"{request['run_id']}.intent.json"
    )
    phase79_receipt, phase79_receipt_sha = _record(
        host.REVIEW_FINAL_REVIEW_SUBMISSION_RECEIPTS_ROOT / f"{request['run_id']}.json"
    )
    if (
        receipt.get("schema_version") != contract.PROTOCOL_VERSION
        or receipt.get("status") != "receipt"
        or receipt.get("intent_sha256") != intent_sha
        or receipt.get("request_sha256") != _sha(_canonical(request))
        or receipt.get("submission_intent_sha256") != phase79_intent_sha
        or receipt.get("submission_receipt_sha256") != phase79_receipt_sha
        or intent.get("request_sha256") != _sha(_canonical(request))
        or intent.get("submission_intent_sha256") != phase79_intent_sha
        or intent.get("submission_receipt_sha256") != phase79_receipt_sha
        or phase79_receipt.get("intent_sha256") != phase79_intent_sha
    ):
        raise FinalReviewObservationError("observation receipt binding invalid")
    # Recheck the exact Phase79 authorization claim and the digest-linked Phase78
    # predecessor records on a provider-free replay.
    phase79_request = _phase79_request(request)
    auth_sha = phase79._validate_authorization(phase79_request)
    claim, claim_sha = _record(
        host.REVIEW_FINAL_REVIEW_SUBMISSION_RECEIPTS_ROOT
        / f"authorization-{request['authorization_id']}.claim.json"
    )
    if claim != phase79._claim_payload(
        phase79_request, auth_sha
    ) or claim_sha != phase79_intent.get("authorization_claim_sha256"):
        raise FinalReviewObservationError("Phase79 authorization claim invalid")
    p78_root = host.REVIEW_PHASE78_RECEIPTS_ROOT
    p78_intent, p78_intent_sha = _record(p78_root / f"{request['run_id']}.intent.json")
    p78_receipt, p78_receipt_sha = _record(p78_root / f"{request['run_id']}.json")
    if (
        p78_intent_sha != phase79_intent.get("phase78_processing_intent_sha256")
        or p78_receipt_sha != phase79_intent.get("phase78_processing_receipt_sha256")
        or p78_receipt.get("intent_sha256") != p78_intent_sha
    ):
        raise FinalReviewObservationError("Phase78 predecessor chain changed")
    run = phase79._run_directory(request)
    owner = (
        host.REVIEW_FINAL_REVIEW_OBSERVATION_WORKER_UID,
        host.REVIEW_FINAL_REVIEW_OBSERVATION_WORKER_GID,
    )
    current = _snapshot(run, owner=owner)
    if _hashes(current) != receipt.get("post_hashes"):
        raise FinalReviewObservationError("observed inventory changed")
    state = _state_document(current[STATE_NAME])
    stored_intent, _ = _record(intent_path)
    pre_hashes = stored_intent.get("pre_hashes")
    if not isinstance(pre_hashes, dict):
        raise FinalReviewObservationError("observation intent invalid")
    for name, digest in pre_hashes.items():
        if name != STATE_NAME and _sha(current.get(name, b"")) != digest:
            raise FinalReviewObservationError("submitted evidence changed")
    estimated = int(phase79_intent["estimated_final_review_cost_microusd"])
    maximum = int(phase79_intent["maximum_authorized_cost_microusd"])
    state_maximum = int(phase79_intent["state_maximum_cost_microusd"])
    expected = _aggregate(
        request,
        state,
        status=str(receipt.get("result", {}).get("status")),
        estimated=estimated,
        state_maximum=state_maximum,
    )
    if expected != receipt.get("result") or maximum != request["maximum_authorized_cost_microusd"]:
        raise FinalReviewObservationError("observation replay aggregate invalid")
    return contract.validate_aggregate(expected)


def process_request(request: Mapping[str, object]) -> dict[str, object]:
    try:
        request = contract.validate_request(request)
        intent_path = (
            host.REVIEW_FINAL_REVIEW_OBSERVATION_RECEIPTS_ROOT
            / f"{request['authorization_id']}.intent.json"
        )
        receipt_path = (
            host.REVIEW_FINAL_REVIEW_OBSERVATION_RECEIPTS_ROOT
            / f"{request['authorization_id']}.json"
        )
        if any(
            os.path.lexists(path.with_name(f".{path.name}.pending"))
            for path in (intent_path, receipt_path)
        ):
            raise FinalReviewObservationError("observation publication ambiguous")
        phase79._directory(
            host.REVIEW_FINAL_REVIEW_OBSERVATION_RECEIPTS_ROOT, mode=0o700, owner=ROOT_OWNER
        )
        replay = _replay(request, intent_path, receipt_path)
        if replay is not None:
            return replay
        # Authenticate all Phase79 records, the application journals, inventories,
        # runtime/configuration binding and Phase78 predecessor before Compose.
        (
            run,
            before,
            before_state_obj,
            submission_intent,
            submission_receipt_sha,
            submission_intent_sha,
        ) = _submission_chain(request)
        before_state = before_state_obj.to_dict()
        estimated = int(submission_intent["estimated_final_review_cost_microusd"])
        state_maximum = int(submission_intent["state_maximum_cost_microusd"])
        authorization_maximum = int(request["maximum_authorized_cost_microusd"])
        if authorization_maximum != int(submission_intent["maximum_authorized_cost_microusd"]):
            raise FinalReviewObservationError("authorization ceiling mismatch")
        prior = phase79._cost_micros(
            before_state["actual_primary_cost_usd"]
        ) + phase79._cost_micros(before_state["actual_adjudication_cost_usd"])
        if prior + estimated > min(authorization_maximum, state_maximum):
            raise FinalReviewObservationError("cost ceiling exceeded")
        intent = {
            "archive_sha256": request["archive_sha256"],
            "authorization_id": request["authorization_id"],
            "operation": contract.OPERATION,
            "request_sha256": _sha(_canonical(request)),
            "run_id": request["run_id"],
            "schema_version": contract.PROTOCOL_VERSION,
            "status": "intent",
            "submission_intent_sha256": submission_intent_sha,
            "submission_receipt_sha256": submission_receipt_sha,
            "pre_hashes": _hashes(before),
            "estimated_final_review_cost_microusd": estimated,
            "maximum_authorized_cost_microusd": authorization_maximum,
            "state_maximum_cost_microusd": state_maximum,
        }
        if intent_path.exists():
            stored_intent, _ = _record(intent_path)
            if stored_intent != intent:
                raise FinalReviewObservationError("observation intent conflict")
        else:
            _write_record(intent_path, intent)
        bindings = _phase80_bindings(
            request, before, receipt_sha=submission_receipt_sha, estimated=estimated
        )
        result = _worker_once(request, run, bindings)
        result = contract.validate_aggregate(result)
        allowed_additions: set[str] = set()
        if result["status"] in {"observed", "already_observed"}:
            allowed_additions.update(
                {"final-review-part-0001-output.jsonl", "final-review-part-0001-api-errors.jsonl"}
            )
        elif result["status"] == "failed":
            allowed_additions.add("terminal-api-errors.jsonl")
        owner = (
            host.REVIEW_FINAL_REVIEW_OBSERVATION_WORKER_UID,
            host.REVIEW_FINAL_REVIEW_OBSERVATION_WORKER_GID,
        )
        after = _snapshot(run, owner=owner)
        after_state = _state_document(after[STATE_NAME])
        before_doc = before_state
        _validate_inventory_transition(before, after, allowed_additions=allowed_additions)
        mutable = {"status", "updated_at", "final_review_completed_part_count"}
        if any(before_doc[key] != after_state[key] for key in before_doc if key not in mutable):
            raise FinalReviewObservationError("observation changed immutable state")
        if after_state["actual_final_review_cost_usd"] != 0:
            raise FinalReviewObservationError("observation changed cost")
        if (
            after_state["status"] != result["run_status"]
            or after_state["final_review_completed_part_count"]
            != result["final_review_completed_part_count"]
        ):
            raise FinalReviewObservationError("observation state/result mismatch")
        if result["status"] in {"observed", "already_observed"} and (
            after_state["status"] != "final_review_submitted"
            or after_state["final_review_completed_part_count"] != 1
            or not after.get("final-review-part-0001-output.jsonl")
        ):
            raise FinalReviewObservationError("part-one output is not durable")
        if result["status"] in {"waiting", "reconciliation_required"} and _hashes(after) != _hashes(
            before
        ):
            raise FinalReviewObservationError("waiting observation mutated the run")
        if result["status"] == "failed" and (
            after_state["status"] != "failed"
            or after_state["final_review_completed_part_count"] != 0
            or not after.get("terminal-api-errors.jsonl")
        ):
            raise FinalReviewObservationError("failed observation state invalid")
        # Construct the outer aggregate from the independently re-read state so
        # worker output cannot widen counts or costs.
        final = _aggregate(
            request,
            after_state,
            status=str(result["status"]),
            estimated=estimated,
            state_maximum=state_maximum,
        )
        if final != result:
            raise FinalReviewObservationError("worker aggregate mismatch")
        if final["status"] not in {"waiting", "reconciliation_required"}:
            _write_record(
                receipt_path,
                {
                    "intent_sha256": _sha(_canonical(intent)),
                    "post_hashes": _hashes(after),
                    "request_sha256": _sha(_canonical(request)),
                    "result": final,
                    "schema_version": contract.PROTOCOL_VERSION,
                    "status": "receipt",
                    "submission_intent_sha256": submission_intent_sha,
                    "submission_receipt_sha256": submission_receipt_sha,
                },
            )
        return final
    except FinalReviewObservationError:
        raise
    except Exception as error:
        raise FinalReviewObservationError("final-review observation rejected") from error


def _require_root() -> None:
    if (
        os.name != "posix"
        or not hasattr(os, "geteuid")
        or os.geteuid() != 0
        or os.environ.get("SUDO_USER") != host.REVIEW_USER
        or platform.system() != "Linux"
        or platform.machine() != "x86_64"
    ):
        raise FinalReviewObservationError("invalid final-review observation caller")


def main() -> int:
    try:
        _require_root()
        result = process_request(_read_request(sys.stdin.buffer))
        sys.stdout.buffer.write(contract.canonical_json(result))
        return 0
    except Exception:
        sys.stderr.write("error=speaker_review_final_review_observation_rejected\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
