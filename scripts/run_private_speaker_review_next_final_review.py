"""Root-only coordinator for authenticated final-review part-two submission."""

# ruff: noqa: E402, I001

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import stat
import subprocess
import sys
import concurrent.futures
from pathlib import Path
from typing import BinaryIO, Mapping

_ROOT = Path(__file__).resolve().parents[1]
for _path in (_ROOT, _ROOT / "src"):
    if os.fspath(_path) not in sys.path:
        sys.path.insert(0, os.fspath(_path))

from scripts import private_speaker_review_next_final_review_host_contract as host  # noqa: E402
from scripts import (
    private_speaker_review_final_review_observation_contract as observation_contract,
)  # noqa: E402
from scripts import (
    private_speaker_review_next_final_review_submission_contract as contract,  # noqa: E402
)
from cinegraph.common.speaker_review_cost_policy import (  # noqa: E402
    BATCH_COMPLETION_WINDOW,
    BATCH_ENDPOINT,
    MAXIMUM_RUN_COST_USD,
    SPEAKER_ADJUDICATION_MODEL,
    SPEAKER_FINAL_REVIEW_MODEL,
    SPEAKER_PRIMARY_REVIEW_MODEL,
    SPEAKER_REVIEW_PROMPT_VERSION,
    SPEAKER_REVIEW_SCHEMA_VERSION,
)
from scripts import run_private_speaker_review_final_review as phase79  # noqa: E402

ROOT_OWNER = (0, 0)
WORKER_OWNER = (host.REVIEW_NEXT_FINAL_REVIEW_WORKER_UID, host.REVIEW_NEXT_FINAL_REVIEW_WORKER_GID)
MAX_RECORD_BYTES = 128 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
STATE_NAME = "run-state.json"
RECEIPT_ROOT = host.REVIEW_NEXT_FINAL_REVIEW_RECEIPTS_ROOT
RUNTIME_BINDING = (BATCH_ENDPOINT, BATCH_COMPLETION_WINDOW)


class NextFinalReviewSubmissionError(RuntimeError):
    """Generic boundary failure with no private evidence in its message."""


def _canonical(value: Mapping[str, object]) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _decode(raw: bytes) -> dict[str, object]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise NextFinalReviewSubmissionError("final-review record invalid") from error
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise NextFinalReviewSubmissionError("final-review record invalid")
    return value


def _directory(path: Path, *, owner: tuple[int, int], mode: int = 0o700) -> None:
    try:
        value = path.lstat()
        if not stat.S_ISDIR(value.st_mode) or stat.S_ISLNK(value.st_mode) or stat.S_IMODE(value.st_mode) != mode or (value.st_uid, value.st_gid) != owner or path.resolve(strict=True) != path:
            raise OSError
    except (OSError, RuntimeError) as error:
        raise NextFinalReviewSubmissionError("final-review evidence unavailable") from error


def _stable(path: Path, *, owner: tuple[int, int], mode: int = 0o600, maximum: int = MAX_FILE_BYTES) -> bytes:
    descriptor = -1
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != mode or (before.st_uid, before.st_gid) != owner or before.st_size <= 0 or before.st_size > maximum:
            raise OSError
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_uid, opened.st_gid) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_uid, before.st_gid):
            raise OSError
        raw = os.read(descriptor, maximum + 1)
        after = path.lstat()
        if len(raw) != opened.st_size or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns):
            raise OSError
        return raw
    except OSError as error:
        raise NextFinalReviewSubmissionError("final-review evidence unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _record(path: Path) -> tuple[dict[str, object], str]:
    raw = _stable(path, owner=ROOT_OWNER, maximum=MAX_RECORD_BYTES)
    return _decode(raw), _sha(raw)


def _write_once(path: Path, value: Mapping[str, object]) -> str:
    encoded = _canonical(value)
    _directory(path.parent, owner=ROOT_OWNER)
    if path.exists():
        existing, digest = _record(path)
        if _canonical(existing) != encoded:
            raise NextFinalReviewSubmissionError("final-review receipt conflict")
        return digest
    pending = path.with_name(f".{path.name}.pending")
    if os.path.lexists(pending):
        raise NextFinalReviewSubmissionError("final-review receipt conflict")
    descriptor = -1
    try:
        descriptor = os.open(pending, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(pending, path, follow_symlinks=False)
        pending.unlink()
    except (FileExistsError, OSError) as error:
        raise NextFinalReviewSubmissionError("final-review receipt unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return _sha(encoded)


def _read_request(stream: BinaryIO) -> dict[str, object]:
    raw = stream.readline(contract.REQUEST_MAX_BYTES + 1)
    if stream.read(1):
        raise NextFinalReviewSubmissionError("invalid final-review request")
    try:
        return contract.parse_request(raw)
    except ValueError as error:
        raise NextFinalReviewSubmissionError("invalid final-review request") from error


def _run(request: Mapping[str, object]) -> Path:
    parent = host.SPEAKER_REVIEW_RUNS_ROOT / f"sha256-{request['archive_sha256']}"
    runs = parent / "review-runs"
    _directory(host.SPEAKER_REVIEW_RUNS_ROOT, owner=ROOT_OWNER)
    _directory(parent, owner=ROOT_OWNER)
    _directory(runs, owner=WORKER_OWNER)
    run = runs / str(request["run_id"])
    _directory(run, owner=WORKER_OWNER)
    if run.resolve(strict=True).parent != runs:
        raise NextFinalReviewSubmissionError("final-review run invalid")
    return run


def _snapshot(run: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    total = 0
    for entry in sorted(run.iterdir(), key=lambda item: item.name):
        raw = _stable(entry, owner=WORKER_OWNER)
        result[entry.name] = raw
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise NextFinalReviewSubmissionError("final-review inventory too large")
    return result


def _hashes(contents: Mapping[str, bytes]) -> dict[str, str]:
    groups = _classes(contents)
    return {
        name: {key: _sha(raw) for key, raw in sorted(group.items())}
        for name, group in zip(("artifacts", "journals", "outputs", "derived"), groups)
    }


def _observation_hashes(contents: Mapping[str, bytes]) -> dict[str, str]:
    """Phase80's receipt uses a flat, exact inventory hash map."""

    return {name: _sha(raw) for name, raw in sorted(contents.items())}


def _state(contents: Mapping[str, bytes]) -> dict[str, object]:
    raw = contents.get(STATE_NAME)
    if raw is None:
        raise NextFinalReviewSubmissionError("final-review state unavailable")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise NextFinalReviewSubmissionError("final-review state invalid") from error
    if not isinstance(value, dict):
        raise NextFinalReviewSubmissionError("final-review state invalid")
    if raw != json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n":
        raise NextFinalReviewSubmissionError("final-review state is not canonical")
    if (
        value.get("status") != "final_review_submitted"
        or type(value.get("schema_version")) is not int
        or type(value.get("final_review_completed_part_count")) is not int
        or value.get("final_review_completed_part_count")
        != contract.PREDECESSOR_COMPLETED_PART_COUNT
        or type(value.get("final_review_part_count")) is not int
        or value["final_review_part_count"] < contract.PREDECESSOR_COMPLETED_PART_COUNT
    ):
        raise NextFinalReviewSubmissionError("final-review checkpoint invalid")
    for key in (
        "maximum_cost_usd",
        "actual_primary_cost_usd",
        "actual_adjudication_cost_usd",
        "actual_final_review_cost_usd",
    ):
        if (
            not isinstance(value.get(key), (int, float))
            or isinstance(value[key], bool)
            or not math.isfinite(float(value[key]))
            or float(value[key]) < 0
        ):
            raise NextFinalReviewSubmissionError("final-review cost state invalid")
    provider_id_fields = (
        "primary_batch_ids",
        "adjudication_batch_ids",
        "final_review_batch_ids",
        "primary_input_file_ids",
        "adjudication_input_file_ids",
        "final_review_input_file_ids",
    )
    if any(
        not isinstance(value.get(key), list)
        or not value[key]
        or any(
            not isinstance(item, str) or not item or item != item.strip()
            for item in value[key]
        )
        or len(set(value[key])) != len(value[key])
        for key in provider_id_fields
    ):
        raise NextFinalReviewSubmissionError("final-review provider IDs invalid")
    batch_ids = (
        set(value["primary_batch_ids"]),
        set(value["adjudication_batch_ids"]),
        set(value["final_review_batch_ids"]),
    )
    input_file_ids = (
        set(value["primary_input_file_ids"]),
        set(value["adjudication_input_file_ids"]),
        set(value["final_review_input_file_ids"]),
    )
    if any(
        groups[left] & groups[right]
        for groups in (batch_ids, input_file_ids)
        for left, right in ((0, 1), (0, 2), (1, 2))
    ):
        raise NextFinalReviewSubmissionError("final-review provider IDs invalid")
    if (
        len(value["final_review_batch_ids"]) not in (1, contract.SUBMITTED_PART_NUMBER)
        or len(value["final_review_input_file_ids"]) not in (1, contract.SUBMITTED_PART_NUMBER)
        or len(value["final_review_batch_ids"]) != len(value["final_review_input_file_ids"])
        or set(value["final_review_batch_ids"]) & set(value["final_review_input_file_ids"])
        or value.get("primary_batch_id") != value["primary_batch_ids"][-1]
        or value.get("primary_input_file_id") != value["primary_input_file_ids"][-1]
        or value.get("adjudication_batch_id") != value["adjudication_batch_ids"][-1]
        or value.get("adjudication_input_file_id")
        != value["adjudication_input_file_ids"][-1]
        or value.get("final_review_batch_id") != value["final_review_batch_ids"][-1]
        or value.get("final_review_input_file_id")
        != value["final_review_input_file_ids"][-1]
        or float(value["actual_final_review_cost_usd"]) != 0.0
        or value.get("actual_total_cost_usd")
        != float(value["actual_primary_cost_usd"])
        + float(value["actual_adjudication_cost_usd"])
        + float(value["actual_final_review_cost_usd"])
    ):
        raise NextFinalReviewSubmissionError("final-review checkpoint invalid")
    return value


def _validate_inventory_names(state: Mapping[str, object], contents: Mapping[str, bytes]) -> None:
    try:
        primary_count = int(state["primary_part_count"])
        adjudication_count = int(state["adjudication_part_count"])
        final_count = int(state["final_review_part_count"])
    except (KeyError, TypeError, ValueError) as error:
        raise NextFinalReviewSubmissionError("final-review inventory invalid") from error
    required = {
        STATE_NAME,
        "candidates.jsonl",
        "source-manifest.json",
        "primary-verdicts.jsonl",
        "primary-parse-errors.json",
        "primary-decisions.jsonl",
        "adjudication-verdicts.jsonl",
        "adjudication-parse-errors.json",
        "final-decisions.jsonl",
        *(f"primary-part-{part:04d}-{suffix}.jsonl" for part in range(1, primary_count + 1) for suffix in ("requests", "output")),
        *(f"adjudication-part-{part:04d}-{suffix}.jsonl" for part in range(1, adjudication_count + 1) for suffix in ("requests", "output")),
        *(f"final-review-part-{part:04d}-requests.jsonl" for part in range(1, final_count + 1)),
        ".final-review-part-0001-submission-intent.json",
        ".final-review-part-0001-submission-completed.json",
        *(f".{stage}-part-{part:04d}-submission-{kind}.json" for stage, total in (("primary", primary_count), ("adjudication", adjudication_count)) for part in range(1, total + 1) for kind in ("intent", "completed")),
        "final-review-part-0001-output.jsonl",
    }
    optional = {
        *(f"primary-part-{part:04d}-api-errors.jsonl" for part in range(1, primary_count + 1)),
        *(f"adjudication-part-{part:04d}-api-errors.jsonl" for part in range(1, adjudication_count + 1)),
        "final-review-part-0001-api-errors.jsonl",
        ".final-review-part-0002-submission-intent.json",
        ".final-review-part-0002-submission-completed.json",
        "final-review-part-0002-output.jsonl",
        "final-review-part-0002-api-errors.jsonl",
    }
    names = set(contents)
    if not required <= names or not names <= required | optional:
        raise NextFinalReviewSubmissionError("final-review inventory invalid")
    if {"final-review-part-0002-output.jsonl", "final-review-part-0002-api-errors.jsonl"} & names:
        raise NextFinalReviewSubmissionError("final-review part-two output is not allowed")


def _validate_transition(
    before: Mapping[str, bytes], after: Mapping[str, bytes],
    before_state: Mapping[str, object], after_state: Mapping[str, object],
) -> None:
    allowed_journals = {
        ".final-review-part-0002-submission-intent.json",
        ".final-review-part-0002-submission-completed.json",
    }
    if any(
        name not in after or after[name] != raw
        for name, raw in before.items()
        if name != STATE_NAME
    ):
        raise NextFinalReviewSubmissionError("final-review immutable evidence changed")
    added = set(after) - set(before)
    if added != allowed_journals:
        raise NextFinalReviewSubmissionError("final-review transition added forbidden evidence")
    if before_state["final_review_completed_part_count"] != after_state["final_review_completed_part_count"]:
        raise NextFinalReviewSubmissionError("final-review completed count changed")
    if before_state["actual_final_review_cost_usd"] != after_state["actual_final_review_cost_usd"]:
        raise NextFinalReviewSubmissionError("final-review cost changed")
    immutable = set(before_state) - {
        "updated_at",
        "status",
        "final_review_batch_id",
        "final_review_input_file_id",
        "final_review_batch_ids",
        "final_review_input_file_ids",
    }
    if any(before_state[key] != after_state.get(key) for key in immutable):
        raise NextFinalReviewSubmissionError("final-review state changed outside transition")
    old_batches = before_state["final_review_batch_ids"]
    new_batches = after_state["final_review_batch_ids"]
    old_inputs = before_state["final_review_input_file_ids"]
    new_inputs = after_state["final_review_input_file_ids"]
    if (
        not isinstance(old_batches, list)
        or not isinstance(new_batches, list)
        or not isinstance(old_inputs, list)
        or not isinstance(new_inputs, list)
        or len(new_batches) != len(old_batches) + 1
        or len(new_inputs) != len(old_inputs) + 1
        or new_batches[:-1] != old_batches
        or new_inputs[:-1] != old_inputs
        or len(set(new_batches)) != len(new_batches)
        or len(set(new_inputs)) != len(new_inputs)
        or after_state["final_review_batch_id"] != new_batches[-1]
        or after_state["final_review_input_file_id"] != new_inputs[-1]
    ):
        raise NextFinalReviewSubmissionError("final-review provider ID transition invalid")


def _set_digest(contents: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(contents):
        encoded = name.encode()
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(contents[name]).to_bytes(8, "big"))
        digest.update(contents[name])
    return digest.hexdigest()


def _classes(contents: Mapping[str, bytes]) -> tuple[dict[str, bytes], ...]:
    groups = ({}, {}, {}, {})
    for name, raw in contents.items():
        if name == STATE_NAME:
            continue
        if name.startswith(".") and "-submission-" in name:
            groups[1][name] = raw
        elif name.endswith("-output.jsonl") or name.endswith("-api-errors.jsonl"):
            groups[2][name] = raw
        elif name.endswith("-requests.jsonl") or name in {"candidates.jsonl", "source-manifest.json"}:
            groups[0][name] = raw
        else:
            groups[3][name] = raw
    return groups


def _bindings(contents: Mapping[str, bytes]) -> dict[str, str]:
    artifacts, journals, outputs, derived = _classes(contents)
    request_name = f"final-review-part-{contract.SUBMITTED_PART_NUMBER:04d}-requests.jsonl"
    if request_name not in contents:
        raise NextFinalReviewSubmissionError("final-review request unavailable")
    return {
        contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: _sha(contents[STATE_NAME]),
        contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: _set_digest(artifacts),
        contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: _set_digest(journals),
        contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256: _set_digest(outputs),
        contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256: _set_digest(derived),
        contract.ENV_EXPECTED_REQUEST_SHA256: _sha(contents[request_name]),
    }


def _micros(value: object) -> int:
    try:
        return phase79._cost_micros(value)
    except phase79.FinalReviewSubmissionError as error:
        raise NextFinalReviewSubmissionError("final-review cost invalid") from error


def _aggregate(state: Mapping[str, object], *, status: str, estimated: int, submitted: int) -> dict[str, object]:
    return contract.validate_aggregate({"actual_adjudication_cost_microusd": _micros(state["actual_adjudication_cost_usd"]), "actual_final_review_cost_microusd": _micros(state["actual_final_review_cost_usd"]), "actual_primary_cost_microusd": _micros(state["actual_primary_cost_usd"]), "estimated_final_review_cost_microusd": estimated, "final_review_completed_part_count": state["final_review_completed_part_count"], "final_review_part_count": state["final_review_part_count"], "operation": contract.OPERATION, "purpose": contract.PURPOSE, "run_id": state["run_id"], "run_status": state["status"], "season_number": contract.SEASON_NUMBER, "status": status, "submitted_part_count": submitted}, status=status)


def _validate_ceiling(
    state: Mapping[str, object], request: Mapping[str, object], estimated: int
) -> None:
    spent = (
        _micros(state["actual_primary_cost_usd"])
        + _micros(state["actual_adjudication_cost_usd"])
        + _micros(state["actual_final_review_cost_usd"])
    )
    state_ceiling = _micros(state["maximum_cost_usd"])
    request_ceiling = request["maximum_authorized_cost_microusd"]
    if type(request_ceiling) is not int or spent + estimated > min(
        request_ceiling, state_ceiling
    ):
        raise NextFinalReviewSubmissionError("final-review cost exceeds authorization")


def _drain(stream: object) -> bytes:
    chunks: list[bytes] = []
    retained = 0
    try:
        while True:
            chunk = stream.read(64 * 1024)  # type: ignore[attr-defined]
            if not chunk:
                break
            if retained <= contract.OUTPUT_MAX_BYTES:
                kept = chunk[: contract.OUTPUT_MAX_BYTES + 1 - retained]
                chunks.append(kept)
                retained += len(kept)
        return b"".join(chunks)
    finally:
        stream.close()  # type: ignore[attr-defined]


def _cleanup_stale_worker(image_reference: str) -> None:
    """Remove only a stale container matching this exact worker contract."""

    try:
        inspected = subprocess.run(
            ["docker", "inspect", "--format", "{{json .}}", host.REVIEW_NEXT_FINAL_REVIEW_CONTAINER_NAME],
            cwd=_ROOT,
            env={"PATH": "/usr/sbin:/usr/bin"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
            timeout=host.REVIEW_NEXT_FINAL_REVIEW_KILL_AFTER_SECONDS,
        )
        if inspected.returncode != 0 or inspected.stderr:
            return
        value = json.loads(inspected.stdout.decode("utf-8"))
        config = value.get("Config", {})
        labels = config.get("Labels", {})
        if (
            value.get("Name") != f"/{host.REVIEW_NEXT_FINAL_REVIEW_CONTAINER_NAME}"
            or config.get("Image") != image_reference
            or config.get("User") != f"{host.REVIEW_NEXT_FINAL_REVIEW_WORKER_UID}:{host.REVIEW_NEXT_FINAL_REVIEW_WORKER_GID}"
            or config.get("WorkingDir") != host.REVIEW_NEXT_FINAL_REVIEW_CONTAINER_WORKDIR
            or config.get("Cmd") != list(host.REVIEW_NEXT_FINAL_REVIEW_CONTAINER_COMMAND)
            or labels.get("com.docker.compose.project")
            != host.REVIEW_NEXT_FINAL_REVIEW_COMPOSE_PROJECT
            or labels.get("com.docker.compose.service")
            != host.REVIEW_NEXT_FINAL_REVIEW_COMPOSE_SERVICE
            or labels.get("com.docker.compose.oneoff") != "True"
        ):
            return
        subprocess.run(
            ["docker", "rm", "--force", host.REVIEW_NEXT_FINAL_REVIEW_CONTAINER_NAME],
            cwd=_ROOT,
            env={"PATH": "/usr/sbin:/usr/bin"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=host.REVIEW_NEXT_FINAL_REVIEW_KILL_AFTER_SECONDS,
        )
    except (OSError, subprocess.SubprocessError, TimeoutError, UnicodeError, json.JSONDecodeError):
        return


def _assert_worker_identity(
    request: Mapping[str, object], run: Path, image_reference: str
) -> None:
    inspected = subprocess.run(
        ["docker", "inspect", "--format", "{{json .}}", host.REVIEW_NEXT_FINAL_REVIEW_CONTAINER_NAME],
        cwd=_ROOT,
        env={"PATH": "/usr/sbin:/usr/bin"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        shell=False,
        timeout=host.REVIEW_NEXT_FINAL_REVIEW_KILL_AFTER_SECONDS,
    )
    if inspected.returncode != 0 or inspected.stderr:
        raise NextFinalReviewSubmissionError("final-review worker identity unavailable")
    try:
        value = json.loads(inspected.stdout.decode("utf-8"))
        config = value["Config"]
        labels = config["Labels"]
        host_config = value["HostConfig"]
        networks = set(value["NetworkSettings"]["Networks"])
        mounts = {
            item["Destination"]: item
            for item in value["Mounts"]
            if isinstance(item, dict) and isinstance(item.get("Destination"), str)
        }
        if (
            value.get("Name") != f"/{host.REVIEW_NEXT_FINAL_REVIEW_CONTAINER_NAME}"
            or config.get("Image") != image_reference
            or config.get("User") != f"{host.REVIEW_NEXT_FINAL_REVIEW_WORKER_UID}:{host.REVIEW_NEXT_FINAL_REVIEW_WORKER_GID}"
            or config.get("WorkingDir") != host.REVIEW_NEXT_FINAL_REVIEW_CONTAINER_WORKDIR
            or config.get("Cmd") != list(host.REVIEW_NEXT_FINAL_REVIEW_CONTAINER_COMMAND)
            or labels.get("com.docker.compose.project")
            != host.REVIEW_NEXT_FINAL_REVIEW_COMPOSE_PROJECT
            or labels.get("com.docker.compose.service")
            != host.REVIEW_NEXT_FINAL_REVIEW_COMPOSE_SERVICE
            or labels.get("com.docker.compose.oneoff") != "True"
            or not host_config.get("ReadonlyRootfs")
            or host_config.get("Privileged")
            or host_config.get("CapDrop") != ["ALL"]
            or host_config.get("SecurityOpt") != ["no-new-privileges:true"]
            or host_config.get("PidsLimit") != 128
            or host_config.get("Memory") != host.REVIEW_NEXT_FINAL_REVIEW_MEMORY_BYTES
            or host_config.get("NanoCpus") != host.REVIEW_NEXT_FINAL_REVIEW_NANO_CPUS
            or host_config.get("RestartPolicy", {}).get("Name") != "no"
            or networks != {host.REVIEW_NEXT_FINAL_REVIEW_NETWORK}
            or mounts.get(host.REVIEW_NEXT_FINAL_REVIEW_SECRET_TARGET, {}).get("RW") is not False
            or mounts.get(host.REVIEW_NEXT_FINAL_REVIEW_TMP_TARGET, {}).get("RW") is not True
            or any("OPENAI_API_KEY=" in str(item) for item in config.get("Env", []))
        ):
            raise ValueError
        run_target = host.REVIEW_NEXT_FINAL_REVIEW_RUNS_TARGET / str(request["run_id"])
        if mounts[run_target.as_posix()].get("Source") != os.fspath(run):
            raise ValueError
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise NextFinalReviewSubmissionError("final-review worker identity invalid") from error


def _worker_once(
    request: Mapping[str, object],
    run: Path,
    bindings: Mapping[str, str],
    *,
    image_reference: str,
) -> dict[str, object]:
    environment = {contract.ENV_ARCHIVE_SHA256: str(request["archive_sha256"]), contract.ENV_RUN_ID: str(request["run_id"]), contract.ENV_AUTHORIZATION_ID: str(request["authorization_id"]), contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: str(request["maximum_authorized_cost_microusd"]), **bindings}
    command = ["docker", "compose", "--progress", "quiet", "--env-file", os.fspath(host.ENV_FILE), "--profile", host.REVIEW_NEXT_FINAL_REVIEW_COMPOSE_PROFILE, "-f", os.fspath(_ROOT / "deploy/compose.yaml"), "run", "--pull", "never", "--no-TTY", "--rm", "--no-deps", "--name", host.REVIEW_NEXT_FINAL_REVIEW_CONTAINER_NAME, host.REVIEW_NEXT_FINAL_REVIEW_COMPOSE_SERVICE]
    for key, value in environment.items():
        command.extend(["-e", f"{key}={value}"])
    command.extend(["-v", f"{run}:{host.REVIEW_NEXT_FINAL_REVIEW_RUNS_TARGET / str(request['run_id'])}:rw"])
    _cleanup_stale_worker(image_reference)
    process = subprocess.Popen(
        command,
        cwd=_ROOT,
        env={"PATH": "/usr/sbin:/usr/bin", **environment},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    if process.stdout is None or process.stderr is None:
        raise NextFinalReviewSubmissionError("final-review worker rejected")
    try:
        _assert_worker_identity(request, run, image_reference)
    except Exception:
        process.kill()
        process.wait()
        _cleanup_stale_worker(image_reference)
        raise
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        stdout_future = pool.submit(_drain, process.stdout)
        stderr_future = pool.submit(_drain, process.stderr)
        try:
            returncode = process.wait(
                timeout=host.REVIEW_NEXT_FINAL_REVIEW_TIMEOUT_SECONDS
            )
        except subprocess.TimeoutExpired as error:
            process.kill()
            process.wait()
            _cleanup_stale_worker(image_reference)
            stdout_future.result(timeout=5)
            stderr_future.result(timeout=5)
            raise NextFinalReviewSubmissionError("final-review worker timed out") from error
        stdout, stderr = stdout_future.result(timeout=5), stderr_future.result(timeout=5)
    if returncode != 0 or stderr or len(stdout) > contract.OUTPUT_MAX_BYTES:
        raise NextFinalReviewSubmissionError("final-review worker rejected")
    try:
        return contract.parse_aggregate(stdout)
    except ValueError as error:
        raise NextFinalReviewSubmissionError("final-review worker response invalid") from error


def _receipt(root: Path, name: str) -> tuple[dict[str, object], str]:
    return _record(root / name)


def _validate_predecessors(
    request: Mapping[str, object], contents: Mapping[str, bytes], state: Mapping[str, object]
) -> tuple[str, str, str, dict[str, object]]:
    phase80, phase80_sha = _receipt(host.REVIEW_PHASE80_RECEIPTS_ROOT, f"{request['authorization_id']}.json")
    observation_request = dict(request)
    observation_request["operation"] = observation_contract.OPERATION
    try:
        observation_request = observation_contract.validate_request(observation_request)
        observation_request_sha = _sha(observation_contract.canonical_json(observation_request))
        observation_result = observation_contract.validate_aggregate(phase80["result"])
    except (KeyError, TypeError, ValueError) as error:
        raise NextFinalReviewSubmissionError("Phase80 observation receipt invalid") from error
    if (
        phase80.get("schema_version") != observation_contract.PROTOCOL_VERSION
        or phase80.get("status") != "receipt"
        or phase80.get("request_sha256") != observation_request_sha
        or phase80.get("result") != observation_result
        or phase80.get("result", {}).get("run_id") != request["run_id"]
        or phase80.get("result", {}).get("status") not in {"observed", "already_observed"}
        or phase80.get("result", {}).get("final_review_completed_part_count")
        != contract.PREDECESSOR_COMPLETED_PART_COUNT
        or phase80.get("result", {}).get("final_review_part_count")
        != state["final_review_part_count"]
        or phase80.get("result", {}).get("maximum_authorized_cost_microusd")
        != request["maximum_authorized_cost_microusd"]
    ):
        raise NextFinalReviewSubmissionError("Phase80 observation receipt invalid")
    phase79_request = dict(request)
    phase79_request["operation"] = phase79.contract.OPERATION
    try:
        phase79_request = phase79.contract.validate_request(phase79_request)
        phase79_request_sha = _sha(phase79.contract.canonical_json(phase79_request))
    except (TypeError, ValueError) as error:
        raise NextFinalReviewSubmissionError("Phase79 submission receipt invalid") from error
    phase79_receipt, phase79_sha = _receipt(host.REVIEW_PHASE79_RECEIPTS_ROOT, f"{request['run_id']}.json")
    try:
        phase79_result = phase79.contract.validate_aggregate(phase79_receipt["result"])
    except (KeyError, TypeError, ValueError) as error:
        raise NextFinalReviewSubmissionError("Phase79 submission receipt invalid") from error
    if (
        phase79_receipt.get("schema_version") != phase79.contract.PROTOCOL_VERSION
        or phase79_receipt.get("status") != "receipt"
        or phase79_receipt.get("request_sha256") != phase79_request_sha
        or phase79_receipt.get("result") != phase79_result
        or phase79_receipt.get("result", {}).get("run_id") != request["run_id"]
        or phase79_receipt.get("result", {}).get("status") not in {"submitted", "already_submitted"}
        or phase79_receipt.get("result", {}).get("final_review_part_count")
        != state["final_review_part_count"]
    ):
        raise NextFinalReviewSubmissionError("Phase79 submission receipt invalid")
    phase80_intent, phase80_intent_sha = _receipt(
        host.REVIEW_PHASE80_RECEIPTS_ROOT,
        f"{request['authorization_id']}.intent.json",
    )
    if (
        phase80.get("intent_sha256") != phase80_intent_sha
        or phase80_intent.get("authorization_id") != request["authorization_id"]
        or phase80_intent.get("run_id") != request["run_id"]
        or phase80_intent.get("status") != "intent"
        or phase80_intent.get("request_sha256") != observation_request_sha
    ):
        raise NextFinalReviewSubmissionError("Phase80 authorization receipt invalid")
    phase79_auth_sha = phase79._validate_authorization(phase79_request)
    phase79_claim, phase79_claim_sha = _receipt(
        host.REVIEW_PHASE79_RECEIPTS_ROOT,
        f"authorization-{request['authorization_id']}.claim.json",
    )
    if (
        phase79_claim != phase79._claim_payload(phase79_request, phase79_auth_sha)
        or phase79_receipt.get("authorization_claim_sha256") != phase79_claim_sha
    ):
        raise NextFinalReviewSubmissionError("Phase79 authorization claim invalid")
    phase79_intent, phase79_intent_sha = _receipt(
        host.REVIEW_PHASE79_RECEIPTS_ROOT, f"{request['run_id']}.intent.json"
    )
    phase79_receipt_intent_sha = phase79_receipt.get("intent_sha256")
    phase78_intent, phase78_intent_sha = _receipt(
        host.REVIEW_PHASE78_RECEIPTS_ROOT, f"{request['run_id']}.intent.json"
    )
    phase78_receipt, phase78_receipt_sha = _receipt(
        host.REVIEW_PHASE78_RECEIPTS_ROOT, f"{request['run_id']}.json"
    )
    if (
        phase79_receipt_intent_sha != phase79_intent_sha
        or phase79_intent.get("phase78_processing_intent_sha256") != phase78_intent_sha
        or phase79_intent.get("phase78_processing_receipt_sha256") != phase78_receipt_sha
        or phase78_receipt.get("intent_sha256") != phase78_intent_sha
    ):
        raise NextFinalReviewSubmissionError("Phase78 predecessor chain invalid")
    if phase80.get("submission_receipt_sha256") != phase79_sha:
        raise NextFinalReviewSubmissionError("Phase80 predecessor binding invalid")
    return phase80_sha, phase79_sha, phase79_auth_sha, phase80


def process_request(request: Mapping[str, object]) -> dict[str, object]:
    request = contract.validate_request(request)
    _directory(RECEIPT_ROOT, owner=ROOT_OWNER)
    run = _run(request)
    before = _snapshot(run)
    state = _state(before)
    if state.get("run_id") != request["run_id"]:
        raise NextFinalReviewSubmissionError("final-review run binding invalid")
    if (
        state.get("schema_version") != SPEAKER_REVIEW_SCHEMA_VERSION
        or state.get("primary_model") != SPEAKER_PRIMARY_REVIEW_MODEL
        or state.get("adjudication_model") != SPEAKER_ADJUDICATION_MODEL
        or state.get("final_review_model") != SPEAKER_FINAL_REVIEW_MODEL
        or state.get("prompt_version") != SPEAKER_REVIEW_PROMPT_VERSION
        or float(state["maximum_cost_usd"]) > MAXIMUM_RUN_COST_USD
    ):
        raise NextFinalReviewSubmissionError("final-review runtime binding invalid")
    try:
        release_sha, image_reference, configuration_sha = phase79._active_runtime_binding()
    except Exception as error:
        raise NextFinalReviewSubmissionError("active runtime binding invalid") from error
    if (BATCH_ENDPOINT, BATCH_COMPLETION_WINDOW) != RUNTIME_BINDING:
        raise NextFinalReviewSubmissionError("final-review endpoint binding invalid")
    _validate_inventory_names(state, before)
    phase80_sha, phase79_sha, phase79_auth_sha, phase80 = _validate_predecessors(
        request, before, state
    )
    estimated = int(phase80["result"]["estimated_final_review_cost_microusd"])
    receipt_path = RECEIPT_ROOT / f"{request['run_id']}.json"
    if len(state["final_review_batch_ids"]) == contract.SUBMITTED_PART_NUMBER and not receipt_path.exists():
        part2_journals = {
            ".final-review-part-0002-submission-intent.json",
            ".final-review-part-0002-submission-completed.json",
        }
        if not part2_journals <= set(before):
            raise NextFinalReviewSubmissionError("final-review retry evidence incomplete")
        _validate_ceiling(state, request, 0)
        return _aggregate(
            state, status="reconciliation_required", estimated=estimated, submitted=0
        )
    if state["final_review_part_count"] == contract.PREDECESSOR_COMPLETED_PART_COUNT and state["final_review_completed_part_count"] == contract.PREDECESSOR_COMPLETED_PART_COUNT:
        observed_hashes = phase80.get("post_hashes")
        if not isinstance(observed_hashes, dict) or observed_hashes != _observation_hashes(before):
            raise NextFinalReviewSubmissionError("observed inventory changed")
        _validate_ceiling(state, request, 0)
        return _aggregate(state, status="all_parts_completed", estimated=0, submitted=0)
    claim_path = RECEIPT_ROOT / f"authorization-{request['authorization_id']}.claim.json"
    if receipt_path.exists():
        receipt, _ = _record(receipt_path)
        claim, claim_sha = _record(claim_path)
        if (
            receipt.get("request_sha256") != _sha(_canonical(request))
            or receipt.get("authorization_claim_sha256") != claim_sha
            or claim.get("request_sha256") != _sha(_canonical(request))
            or claim.get("phase80_observation_receipt_sha256") != phase80_sha
            or claim.get("phase79_submission_receipt_sha256") != phase79_sha
            or claim.get("phase79_authorization_sha256") != phase79_auth_sha
            or receipt.get("release_sha") != release_sha
            or receipt.get("image_reference") != image_reference
            or receipt.get("configuration_sha256") != configuration_sha
        ):
            raise NextFinalReviewSubmissionError("final-review receipt conflict")
        post_hashes = receipt.get("post_hashes")
        if not isinstance(post_hashes, dict) or post_hashes != _hashes(before):
            raise NextFinalReviewSubmissionError("final-review receipt inventory changed")
        replay = contract.validate_aggregate(receipt["result"])
        if _aggregate(
            state,
            status=str(replay["status"]),
            estimated=int(replay["estimated_final_review_cost_microusd"]),
            submitted=int(replay["submitted_part_count"]),
        ) != replay:
            raise NextFinalReviewSubmissionError("final-review replay aggregate invalid")
        return replay
    observed_hashes = phase80.get("post_hashes")
    if not isinstance(observed_hashes, dict) or observed_hashes != _observation_hashes(before):
        raise NextFinalReviewSubmissionError("observed inventory changed")
    claim = {
        "archive_sha256": request["archive_sha256"],
        "authorization_id": request["authorization_id"],
        "maximum_authorized_cost_microusd": request["maximum_authorized_cost_microusd"],
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "request_sha256": _sha(_canonical(request)),
        "run_id": request["run_id"],
        "schema_version": contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
        "status": "claimed",
        "phase80_observation_receipt_sha256": phase80_sha,
        "phase79_submission_receipt_sha256": phase79_sha,
        "phase79_authorization_sha256": phase79_auth_sha,
        "release_sha": release_sha,
        "image_reference": image_reference,
        "configuration_sha256": configuration_sha,
    }
    claim_sha = _write_once(claim_path, claim)
    intent_path = RECEIPT_ROOT / f"{request['run_id']}.intent.json"
    if intent_path.exists():
        return _aggregate(
            state, status="reconciliation_required", estimated=estimated, submitted=0
        )
    bindings = _bindings(before)
    intent = {"archive_sha256": request["archive_sha256"], "authorization_id": request["authorization_id"], "authorization_claim_sha256": claim_sha, "configuration_sha256": configuration_sha, "image_reference": image_reference, "operation": contract.OPERATION, "release_sha": release_sha, "request_sha256": _sha(_canonical(request)), "run_id": request["run_id"], "schema_version": contract.PROTOCOL_VERSION, "status": "intent", "phase80_observation_receipt_sha256": phase80_sha, "phase79_submission_receipt_sha256": phase79_sha, "phase79_authorization_sha256": phase79_auth_sha, **bindings}
    intent_sha = _write_once(intent_path, intent)
    result = contract.validate_aggregate(
        _worker_once(request, run, bindings, image_reference=image_reference)
    )
    after = _snapshot(run)
    after_state = _state(after)
    _validate_inventory_names(after_state, after)
    final = contract.validate_aggregate(result)
    _validate_ceiling(
        state,
        request,
        int(final["estimated_final_review_cost_microusd"]),
    )
    _validate_transition(before, after, state, after_state)
    reread = _aggregate(
        after_state,
        status=str(final["status"]),
        estimated=int(final["estimated_final_review_cost_microusd"]),
        submitted=int(final["submitted_part_count"]),
    )
    if reread != final:
        raise NextFinalReviewSubmissionError("final-review aggregate changed")
    if final["status"] in {"submitted", "already_submitted"}:
        _write_once(receipt_path, {"authorization_claim_sha256": claim_sha, "configuration_sha256": configuration_sha, "image_reference": image_reference, "intent_sha256": intent_sha, "request_sha256": _sha(_canonical(request)), "pre_hashes": _hashes(before), "post_hashes": _hashes(after), "release_sha": release_sha, "result": final, "schema_version": contract.PROTOCOL_VERSION, "status": "receipt", "phase80_observation_receipt_sha256": phase80_sha, "phase79_submission_receipt_sha256": phase79_sha, "phase79_authorization_sha256": phase79_auth_sha})
    return final


def _require_root() -> None:
    if os.name != "posix" or os.geteuid() != 0 or os.environ.get("SUDO_USER") != host.REVIEW_USER or platform.system() != "Linux" or platform.machine() != "x86_64":
        raise NextFinalReviewSubmissionError("invalid final-review caller")


def main() -> int:
    try:
        _require_root()
        sys.stdout.buffer.write(contract.canonical_json(process_request(_read_request(sys.stdin.buffer))))
        return 0
    except Exception:
        sys.stderr.write("error=speaker_review_next_final_review_submission_rejected\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
