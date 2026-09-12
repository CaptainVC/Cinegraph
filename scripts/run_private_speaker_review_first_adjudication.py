"""Root coordinator for the one explicitly authorised first adjudication part."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import platform
import signal
import stat
import subprocess
import sys
from pathlib import Path
from typing import BinaryIO, Final, Mapping

_ROOT = Path(__file__).resolve().parents[1]
if os.fspath(_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(_ROOT))

from scripts import (  # noqa: E402
    private_speaker_review_first_adjudication_host_contract as host,
)
from scripts import (  # noqa: E402
    private_speaker_review_first_adjudication_submission_contract as contract,
)
from scripts import run_private_speaker_review_next_primary as predecessor  # noqa: E402
from scripts import run_private_speaker_review_primary_result_processing as phase68  # noqa: E402


class FirstAdjudicationSubmissionError(RuntimeError):
    """Generic, path-free rejection used at the root/provider boundary."""


RELEASE_ROOT: Final = _ROOT
RUNS_ROOT: Final = host.SPEAKER_REVIEW_RUNS_ROOT
AUTHORIZATION_ROOT: Final = host.REVIEW_AUTHORIZATION_ROOT
PROCESSING_RECEIPTS_ROOT: Final = host.SPEAKER_REVIEW_ROOT / "primary-result-processing-receipts"
RECEIPTS_ROOT: Final = host.REVIEW_FIRST_ADJUDICATION_RECEIPTS_ROOT
ENV_FILE: Final = host.ENV_FILE
COMPOSE_PATH: Final = RELEASE_ROOT / "deploy/compose.yaml"
ROOT_UID: Final = 0
ROOT_GID: Final = 0
WORKER_UID: Final = host.REVIEW_FIRST_ADJUDICATION_WORKER_UID
WORKER_GID: Final = host.REVIEW_FIRST_ADJUDICATION_WORKER_GID
MAX_RECORD_BYTES: Final = 128 * 1024
MAX_STATE_BYTES: Final = 64 * 1024
MAX_FILE_BYTES: Final = 64 * 1024 * 1024
MAX_TOTAL_BYTES: Final = 256 * 1024 * 1024
WORKER_SHUTDOWN_SECONDS: Final = 60
WORKER_TIMEOUT_SECONDS: Final = (
    host.REVIEW_FIRST_ADJUDICATION_TIMEOUT_SECONDS - WORKER_SHUTDOWN_SECONDS
)
WORKER_KILL_AFTER_SECONDS: Final = host.REVIEW_FIRST_ADJUDICATION_KILL_AFTER_SECONDS
STATE_NAME: Final = "run-state.json"
REQUEST_NAME: Final = "adjudication-part-0001-requests.jsonl"
INTENT_NAME: Final = ".adjudication-part-0001-submission-intent.json"
COMPLETED_NAME: Final = ".adjudication-part-0001-submission-completed.json"
_ROOT_INTENT_KEYS = frozenset(
    {
        "archive_sha256",
        "authorization_id",
        "authorization_sha256",
        "configuration_sha256",
        "image_reference",
        "maximum_authorized_cost_microusd",
        "operation",
        "prep_receipt_sha256",
        "processing_receipt_sha256",
        "purpose",
        "release_sha",
        "run_id",
        "request_sha256",
        "schema_version",
        "season_number",
        "status",
        "pre_state_sha256",
        "pre_artifact_set_sha256",
        "pre_journal_set_sha256",
        "pre_output_set_sha256",
        "pre_derived_set_sha256",
        "pre_updated_at",
    }
)
_ROOT_RECEIPT_KEYS = _ROOT_INTENT_KEYS | {"post_state_sha256", "post_journal_set_sha256", "result"}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_nlink,
        stat.S_IMODE(value.st_mode),
        value.st_uid,
        value.st_gid,
    )


def _stable(
    path: Path,
    *,
    maximum: int,
    mode: int = 0o600,
    owner: tuple[int, int] | None = None,
) -> bytes:
    expected_owner = (ROOT_UID, ROOT_GID) if owner is None else owner
    descriptor = -1
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > maximum
            or (os.name == "posix" and stat.S_IMODE(before.st_mode) != mode)
            or (os.name == "posix" and (before.st_uid, before.st_gid) != expected_owner)
        ):
            raise OSError
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        raw = os.read(descriptor, maximum + 1)
        after = path.lstat()
        if (
            _identity(before) != _identity(opened)
            or _identity(opened) != _identity(after)
            or len(raw) != opened.st_size
        ):
            raise OSError
        return raw
    except OSError as error:
        raise FirstAdjudicationSubmissionError("adjudication evidence unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _repair_linked_publication(path: Path) -> None:
    """Finish the sole safe hard-link publication state after a crash."""

    pending = path.with_name(f".{path.name}.pending")
    if not os.path.lexists(pending):
        return
    try:
        published = path.lstat()
        staging = pending.lstat()
        if (
            not stat.S_ISREG(published.st_mode)
            or not stat.S_ISREG(staging.st_mode)
            or stat.S_ISLNK(published.st_mode)
            or stat.S_ISLNK(staging.st_mode)
            or (published.st_dev, published.st_ino) != (staging.st_dev, staging.st_ino)
            or published.st_nlink != 2
            or staging.st_nlink != 2
            or (os.name == "posix" and stat.S_IMODE(published.st_mode) != 0o600)
            or (os.name == "posix" and (published.st_uid, published.st_gid) != (ROOT_UID, ROOT_GID))
        ):
            raise OSError
        pending.unlink()
        _fsync_directory(path.parent)
    except OSError as error:
        raise FirstAdjudicationSubmissionError("adjudication record unavailable") from error


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _decode(raw: bytes) -> dict[str, object]:
    try:
        value = json.loads(
            raw.decode(), parse_constant=lambda _: (_ for _ in ()).throw(ValueError())
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise FirstAdjudicationSubmissionError("adjudication evidence invalid") from error
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise FirstAdjudicationSubmissionError("adjudication evidence invalid")
    return value


def _directory(path: Path, *, mode: int, owner: tuple[int, int] = (0, 0)) -> None:
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or path.resolve(strict=True) != path
            or stat.S_IMODE(metadata.st_mode) != mode
            or (metadata.st_uid, metadata.st_gid) != owner
        ):
            raise OSError
    except OSError as error:
        raise FirstAdjudicationSubmissionError("adjudication evidence unavailable") from error


def _record(path: Path) -> tuple[dict[str, object], str]:
    _repair_linked_publication(path)
    raw = _stable(path, maximum=MAX_RECORD_BYTES)
    return _decode(raw), _sha(raw)


def _read_request(stream: BinaryIO) -> dict[str, object]:
    raw = stream.readline(contract.REQUEST_MAX_BYTES + 1)
    if stream.read(1):
        raise FirstAdjudicationSubmissionError("invalid adjudication request")
    try:
        return contract.parse_request(raw)
    except (TypeError, ValueError) as error:
        raise FirstAdjudicationSubmissionError("invalid adjudication request") from error


def _validate_authorization(request: Mapping[str, object]) -> str:
    _directory(AUTHORIZATION_ROOT, mode=0o700)
    path = AUTHORIZATION_ROOT / f"{request['authorization_id']}.json"
    raw = _stable(path, maximum=contract.REQUEST_MAX_BYTES)
    try:
        if contract.parse_request(raw) != dict(request):
            raise ValueError
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise FirstAdjudicationSubmissionError("adjudication authorization invalid") from error
    return _sha(raw)


def _run(request: Mapping[str, object]) -> Path:
    _directory(RUNS_ROOT, mode=0o700)
    root = RUNS_ROOT / f"sha256-{request['archive_sha256']}"
    _directory(root, mode=0o700)
    runs = root / "review-runs"
    _directory(runs, mode=0o700, owner=(WORKER_UID, WORKER_GID))
    run = runs / str(request["run_id"])
    try:
        if run.resolve(strict=False).parent != runs.resolve(strict=True):
            raise OSError
    except OSError as error:
        raise FirstAdjudicationSubmissionError("adjudication run invalid") from error
    _directory(run, mode=0o700, owner=(WORKER_UID, WORKER_GID))
    return run


def _inventory(
    run: Path,
) -> tuple[
    dict[str, bytes], dict[str, bytes], dict[str, bytes], dict[str, bytes], dict[str, object]
]:
    _directory(run, mode=0o700, owner=(WORKER_UID, WORKER_GID))
    all_files: dict[str, bytes] = {}
    total = 0
    directories: dict[str, tuple[int, ...]] = {}
    for current, dirs, names in os.walk(run, followlinks=False):
        current_path = Path(current)
        metadata = current_path.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or (metadata.st_uid, metadata.st_gid) != (WORKER_UID, WORKER_GID)
        ):
            raise FirstAdjudicationSubmissionError("adjudication inventory invalid")
        directories[current_path.relative_to(run).as_posix()] = _identity(metadata)
        for child in dirs:
            child_meta = (current_path / child).lstat()
            if not stat.S_ISDIR(child_meta.st_mode) or stat.S_ISLNK(child_meta.st_mode):
                raise FirstAdjudicationSubmissionError("adjudication inventory invalid")
        for name in names:
            relative = (Path(current) / name).relative_to(run).as_posix()
            raw = _stable(
                run / relative,
                maximum=MAX_STATE_BYTES if relative == STATE_NAME else MAX_FILE_BYTES,
                owner=(WORKER_UID, WORKER_GID),
            )
            total += len(raw)
            if total > MAX_TOTAL_BYTES:
                raise FirstAdjudicationSubmissionError("adjudication inventory too large")
            all_files[relative] = raw
    for name, identity in directories.items():
        path = run if name == "." else run / name
        if _identity(path.lstat()) != identity:
            raise FirstAdjudicationSubmissionError("adjudication inventory invalid")
    if STATE_NAME not in all_files:
        raise FirstAdjudicationSubmissionError("adjudication state unavailable")
    state = _decode(all_files[STATE_NAME])
    artifacts: dict[str, bytes] = {}
    journals: dict[str, bytes] = {}
    outputs: dict[str, bytes] = {}
    derived: dict[str, bytes] = {}
    for name, raw in all_files.items():
        if name == STATE_NAME:
            continue
        if name.startswith(".primary-part-") or (name in {INTENT_NAME, COMPLETED_NAME}):
            journals[name] = raw
        elif name in {"candidates.jsonl", "source-manifest.json"} or (
            name.startswith("primary-part-") and name.endswith("-requests.jsonl")
        ):
            artifacts[name] = raw
        elif name.startswith("primary-part-") and (
            name.endswith("-output.jsonl") or name.endswith("-api-errors.jsonl")
        ):
            outputs[name] = raw
        else:
            # Adjudication request parts and primary verdict/parse/decision
            # products are derived evidence for this transition.
            derived[name] = raw
    derived.update({f"{name}/": b"" for name in directories if name != "."})
    return all_files, artifacts, journals, outputs, {"state": state, "derived": derived}


def _set_digest(contents: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(contents):
        encoded = name.encode()
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(contents[name]).to_bytes(8, "big"))
        digest.update(contents[name])
    return digest.hexdigest()


def _validate_pre_adjudication(state: Mapping[str, object], request_raw: bytes) -> None:
    """Require the prepared checkpoint to have no provider adjudication IDs."""
    try:
        lines = request_raw.splitlines()
        if not lines:
            raise ValueError
        for line in lines:
            value = json.loads(line.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError
        for key in (
            "adjudication_batch_id",
            "adjudication_input_file_id",
            "adjudication_batch_ids",
            "adjudication_input_file_ids",
        ):
            value = state.get(key)
            if value not in (None, "", []):
                raise ValueError
    except (UnicodeError, json.JSONDecodeError, ValueError, TypeError) as error:
        raise FirstAdjudicationSubmissionError("adjudication checkpoint invalid") from error


def _validate_result(result: object, *, status: str, maximum: int) -> dict[str, object]:
    """Validate the worker aggregate and enforce the fresh authorization cap."""
    try:
        value = contract.validate_aggregate(result, status=status)
        actual = value["actual_primary_cost_microusd"]
        estimate = value["estimated_adjudication_cost_microusd"]
        if actual + estimate > maximum:
            raise ValueError
        return value
    except (TypeError, ValueError, KeyError) as error:
        raise FirstAdjudicationSubmissionError("adjudication aggregate invalid") from error


def _preparation(request: Mapping[str, object]) -> tuple[dict[str, object], str]:
    try:
        from scripts import run_private_speaker_review_next_primary as predecessor

        prepared, receipt_sha = predecessor._validate_preparation(request)
        return prepared, receipt_sha
    except Exception as error:
        raise FirstAdjudicationSubmissionError("preparation evidence invalid") from error


def _phase68(
    request: Mapping[str, object],
    run: Path,
    state: Mapping[str, object],
    all_files: Mapping[str, bytes],
    artifacts: Mapping[str, bytes],
    journals: Mapping[str, bytes],
    outputs: Mapping[str, bytes],
    derived: Mapping[str, bytes],
    preparation: Mapping[str, object],
) -> tuple[str, dict[str, object]]:
    _directory(PROCESSING_RECEIPTS_ROOT, mode=0o700)
    try:
        snapshot = phase68.RunSnapshot(
            all_files[STATE_NAME], dict(artifacts), dict(journals), dict(outputs), dict(derived)
        )
        phase68_state = phase68._state(snapshot)
        phase68._validate_inventory_shape(snapshot, phase68_state)
        if (
            phase68_state != state
            or snapshot.artifacts != artifacts
            or snapshot.journals != journals
            or snapshot.outputs != outputs
            or snapshot.derived != derived
        ):
            raise ValueError
        intent, _ = phase68._read_record(
            PROCESSING_RECEIPTS_ROOT / f"{request['run_id']}.intent.json"
        )
        receipt, receipt_sha = phase68._read_record(
            PROCESSING_RECEIPTS_ROOT / f"{request['run_id']}.json"
        )
        processing_request = {
            "archive_sha256": request["archive_sha256"],
            "authorization_id": intent["authorization_id"],
            "maximum_authorized_cost_microusd": intent["maximum_authorized_cost_microusd"],
            "operation": phase68.contract.OPERATION,
            "purpose": phase68.contract.PURPOSE,
            "run_id": request["run_id"],
            "schema_version": phase68.contract.PROTOCOL_VERSION,
            "season_number": phase68.contract.SEASON_NUMBER,
        }
        auth_raw = _stable(
            AUTHORIZATION_ROOT / f"{intent['authorization_id']}.json",
            maximum=contract.REQUEST_MAX_BYTES,
        )
        if phase68.contract.parse_request(auth_raw) != processing_request:
            raise ValueError
        validated_intent = phase68._validate_intent(
            intent, request=processing_request, prep=preparation, authorization_sha=_sha(auth_raw)
        )
        phase68._validate_predecessor_replay(
            validated_intent, processing_request, preparation, state
        )
        result = phase68._aggregate(processing_request, state, "adjudication_prepared")
        phase68._validate_receipt(
            receipt,
            intent=validated_intent,
            snapshot=snapshot,
            expected_status="adjudication_prepared",
            expected_result=result,
        )
        return receipt_sha, result
    except Exception as error:
        raise FirstAdjudicationSubmissionError("processing evidence invalid") from error


def _env(request: Mapping[str, object], bindings: Mapping[str, str]) -> dict[str, str]:
    result = {
        contract.ENV_ARCHIVE_SHA256: str(request["archive_sha256"]),
        contract.ENV_RUN_ID: str(request["run_id"]),
        contract.ENV_AUTHORIZATION_ID: str(request["authorization_id"]),
        contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: str(
            request["maximum_authorized_cost_microusd"]
        ),
    }
    result.update(bindings)
    return result


def _run_worker(
    request: Mapping[str, object], run: Path, bindings: Mapping[str, str]
) -> dict[str, object]:
    # Compose interpolation and the worker receive only the pinned request
    # bindings; no caller environment or plaintext credential crosses here.
    command = _worker_args(request, run, bindings)
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            command,
            cwd=RELEASE_ROOT,
            env={"PATH": "/usr/sbin:/usr/bin", "HOME": "/root"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=True,
        )
        if process.stdout is None or process.stderr is None:
            raise OSError
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            stdout_future = executor.submit(_read_bounded, process.stdout)
            stderr_future = executor.submit(_read_bounded, process.stderr)
            try:
                returncode = process.wait(timeout=WORKER_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as error:
                _terminate_worker(process)
                stdout_future.result(timeout=5)
                stderr_future.result(timeout=5)
                raise FirstAdjudicationSubmissionError("adjudication worker timeout") from error
            stdout = stdout_future.result(timeout=5)
            stderr = stderr_future.result(timeout=5)
    except FirstAdjudicationSubmissionError:
        raise
    except (OSError, subprocess.SubprocessError, TimeoutError) as error:
        raise FirstAdjudicationSubmissionError("adjudication worker unavailable") from error
    finally:
        if process is not None and process.poll() is None:
            _terminate_worker(process)
    if returncode != 0 or stderr or len(stdout) > contract.OUTPUT_MAX_BYTES:
        raise FirstAdjudicationSubmissionError("adjudication worker rejected")
    try:
        return contract.parse_aggregate(stdout)
    except (TypeError, ValueError) as error:
        raise FirstAdjudicationSubmissionError("adjudication aggregate invalid") from error


def _worker_args(
    request: Mapping[str, object], run: Path, bindings: Mapping[str, str]
) -> list[str]:
    environment = _env(request, bindings)
    command = [
        "docker",
        "compose",
        "--progress",
        "quiet",
        "--env-file",
        str(ENV_FILE),
        "--profile",
        host.REVIEW_FIRST_ADJUDICATION_COMPOSE_PROFILE,
        "-f",
        str(COMPOSE_PATH),
        "run",
        "--rm",
        "--no-deps",
        "--no-TTY",
        "--pull",
        "never",
        "--name",
        host.REVIEW_FIRST_ADJUDICATION_CONTAINER_NAME,
        "--user",
        f"{WORKER_UID}:{WORKER_GID}",
    ]
    for name, value in sorted(environment.items()):
        command.extend(("--env", f"{name}={value}"))
    command.extend(
        (
            "-v",
            # The worker resolves PRIVATE_REVIEW_RUNS_ROOT/run_id; mount its
            # digest-selected review-runs parent, never the run itself.
            f"{run.parent}:{host.REVIEW_FIRST_ADJUDICATION_RUNS_TARGET}:rw",
            host.REVIEW_FIRST_ADJUDICATION_COMPOSE_SERVICE,
        )
    )
    return command


def _terminate_worker(process: subprocess.Popen[bytes]) -> None:
    if hasattr(os, "killpg"):
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=WORKER_KILL_AFTER_SECONDS)
            return
        except (ProcessLookupError, subprocess.TimeoutExpired, OSError):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
    try:
        process.kill()
        process.wait()
    except OSError:
        pass


def _read_bounded(stream: BinaryIO) -> bytes:
    try:
        return stream.read(contract.OUTPUT_MAX_BYTES + 1)
    finally:
        stream.close()


def _write_once(path: Path, value: Mapping[str, object]) -> None:
    _directory(RECEIPTS_ROOT, mode=0o700)
    raw = _canonical(value)
    if len(raw) > MAX_RECORD_BYTES:
        raise FirstAdjudicationSubmissionError("adjudication receipt unavailable")
    pending = path.with_name(f".{path.name}.pending")
    if os.path.lexists(path):
        _repair_linked_publication(path)
        existing, _ = _record(path)
        if existing != dict(value):
            raise FirstAdjudicationSubmissionError("adjudication receipt conflict")
        return
    if os.path.lexists(pending):
        if _stable(pending, maximum=MAX_RECORD_BYTES) != raw:
            raise FirstAdjudicationSubmissionError("adjudication receipt conflict")
    else:
        descriptor = -1
        try:
            descriptor = os.open(
                pending,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            _fsync_directory(RECEIPTS_ROOT)
        except OSError as error:
            raise FirstAdjudicationSubmissionError("adjudication receipt unavailable") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    try:
        os.link(pending, path, follow_symlinks=False)
        _fsync_directory(RECEIPTS_ROOT)
        pending.unlink()
        _fsync_directory(RECEIPTS_ROOT)
    except FileExistsError:
        existing, _ = _record(path)
        if existing != dict(value):
            raise FirstAdjudicationSubmissionError("adjudication receipt conflict") from None
        pending.unlink(missing_ok=True)
    except OSError as error:
        raise FirstAdjudicationSubmissionError("adjudication receipt unavailable") from error


def _checkpoint_bindings(files, artifacts, journals, outputs, derived):
    return {
        contract.ENV_EXPECTED_REQUEST_SHA256: _sha(files[REQUEST_NAME]),
        contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: _sha(files[STATE_NAME]),
        contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: _set_digest(artifacts),
        contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: _set_digest(journals),
        contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256: _set_digest(outputs),
        contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256: _set_digest(derived),
    }


def _prepared_state(state, intent):
    """Recover only the fields changed by the first submission."""
    result = dict(state)
    if state.get("status") == "adjudication_submitted":
        if intent is None:
            raise FirstAdjudicationSubmissionError("submission intent missing")
        result.update(
            status="adjudication_prepared",
            updated_at=intent["pre_updated_at"],
            adjudication_batch_id=None,
            adjudication_input_file_id=None,
            adjudication_batch_ids=[],
            adjudication_input_file_ids=[],
        )
    return result


def _expected_result(state, result, maximum, status):
    result = _validate_result(result, status=status, maximum=maximum)
    from decimal import ROUND_CEILING, Decimal

    actual = int(
        (Decimal(str(state["actual_primary_cost_usd"])) * 1_000_000).to_integral_value(
            rounding=ROUND_CEILING
        )
    )
    if (
        result["run_id"] != state["run_id"]
        or result["run_status"] != state["status"]
        or result["adjudication_part_count"] != state["adjudication_part_count"]
        or result["actual_primary_cost_microusd"] != actual
    ):
        raise FirstAdjudicationSubmissionError("adjudication aggregate mismatch")
    return result


def _validate_submission_journals(
    state: Mapping[str, object], journals: Mapping[str, bytes], request_sha256: str
) -> None:
    try:
        intent = _decode(journals[INTENT_NAME])
        completed = _decode(journals[COMPLETED_NAME])
        binding = intent["binding"]
        if (
            set(intent) != {"binding", "status"}
            or intent["status"] != "intent"
            or set(completed) != {"batch_id", "binding", "input_file_id", "status"}
            or completed["binding"] != binding
            or not isinstance(binding, dict)
            or set(binding)
            != {
                "batch_endpoint",
                "completion_window",
                "part",
                "prompt_version",
                "request_sha256",
                "run_id",
                "schema_version",
                "stage",
            }
            or type(binding["schema_version"]) is not int
            or binding["schema_version"] != 1
            or binding["request_sha256"] != request_sha256
            or binding["run_id"] != state["run_id"]
            or binding["stage"] != "adjudication"
            or binding["part"] != 1
            or binding["prompt_version"] != state["prompt_version"]
            or not all(
                isinstance(binding[key], str) and binding[key]
                for key in ("batch_endpoint", "completion_window")
            )
            or not all(
                isinstance(completed[key], str)
                and completed[key]
                and completed[key] == completed[key].strip()
                for key in ("batch_id", "input_file_id", "status")
            )
            or state["adjudication_batch_id"] != completed["batch_id"]
            or state["adjudication_input_file_id"] != completed["input_file_id"]
            or state["adjudication_batch_ids"] != [completed["batch_id"]]
            or state["adjudication_input_file_ids"] != [completed["input_file_id"]]
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise FirstAdjudicationSubmissionError("adjudication journals invalid") from error


def process_request(request: Mapping[str, object]) -> dict[str, object]:
    try:
        request = contract.validate_request(request)
        authorization_sha = _validate_authorization(request)
        preparation, preparation_sha = _preparation(request)
        phase68._source_workspace(request)
        run = _run(request)
        files, artifacts, journals, outputs, extra = _inventory(run)
        state, derived = extra["state"], extra["derived"]
        if state.get("status") not in {"adjudication_prepared", "adjudication_submitted"}:
            raise ValueError
        _directory(RECEIPTS_ROOT, mode=0o700)
        intent_path = RECEIPTS_ROOT / f"{request['run_id']}.intent.json"
        receipt_path = RECEIPTS_ROOT / f"{request['run_id']}.json"
        intent = _record(intent_path)[0] if os.path.lexists(intent_path) else None
        receipt = _record(receipt_path)[0] if os.path.lexists(receipt_path) else None
        if receipt is not None and intent is None:
            raise ValueError
        if intent is not None and set(intent) != _ROOT_INTENT_KEYS:
            raise ValueError
        new_journals = {
            name: raw for name, raw in journals.items() if name in {INTENT_NAME, COMPLETED_NAME}
        }
        if new_journals and intent is None:
            raise ValueError
        original_journals = {
            name: raw for name, raw in journals.items() if name not in new_journals
        }
        prepared = _prepared_state(state, intent)
        prepared_raw = _canonical(prepared)
        prior_files = {**files, STATE_NAME: prepared_raw}
        for name in new_journals:
            prior_files.pop(name)
        processing_sha, _ = _phase68(
            request,
            run,
            prepared,
            prior_files,
            artifacts,
            original_journals,
            outputs,
            derived,
            preparation,
        )
        _validate_pre_adjudication(prepared, files[REQUEST_NAME])
        expected_intent = {
            "archive_sha256": request["archive_sha256"],
            "authorization_id": request["authorization_id"],
            "authorization_sha256": authorization_sha,
            "configuration_sha256": preparation["config_sha"],
            "image_reference": preparation["image"],
            "maximum_authorized_cost_microusd": request["maximum_authorized_cost_microusd"],
            "operation": contract.OPERATION,
            "prep_receipt_sha256": preparation_sha,
            "processing_receipt_sha256": processing_sha,
            "purpose": contract.PURPOSE,
            "release_sha": preparation["release_sha"],
            "run_id": request["run_id"],
            "request_sha256": _sha(files[REQUEST_NAME]),
            "schema_version": contract.PROTOCOL_VERSION,
            "season_number": contract.SEASON_NUMBER,
            "status": "intent",
            "pre_updated_at": prepared["updated_at"],
            "pre_state_sha256": _sha(prepared_raw),
            "pre_artifact_set_sha256": _set_digest(artifacts),
            "pre_journal_set_sha256": _set_digest(original_journals),
            "pre_output_set_sha256": _set_digest(outputs),
            "pre_derived_set_sha256": _set_digest(derived),
        }
        if set(expected_intent) != _ROOT_INTENT_KEYS:
            raise ValueError
        if intent is None:
            _write_once(intent_path, expected_intent)
            intent = expected_intent
        elif intent != expected_intent:
            raise ValueError
        maximum = int(request["maximum_authorized_cost_microusd"])
        if receipt is not None:
            if (
                state["status"] != "adjudication_submitted"
                or set(receipt) != _ROOT_RECEIPT_KEYS
                or any(
                    receipt.get(key) != value for key, value in intent.items() if key != "status"
                )
                or receipt["status"] != "receipt"
                or receipt["post_state_sha256"] != _sha(files[STATE_NAME])
                or receipt["post_journal_set_sha256"] != _set_digest(journals)
            ):
                raise ValueError
            _expected_result(state, receipt["result"], maximum, "submitted")
        bindings = _checkpoint_bindings(files, artifacts, journals, outputs, derived)
        result = _run_worker(request, run, bindings)
        after_files, after_artifacts, after_journals, after_outputs, after_extra = _inventory(run)
        after_state = after_extra["state"]
        after_original = {
            name: raw
            for name, raw in after_journals.items()
            if name not in {INTENT_NAME, COMPLETED_NAME}
        }
        if (
            artifacts != after_artifacts
            or original_journals != after_original
            or outputs != after_outputs
            or derived != after_extra["derived"]
            or _prepared_state(after_state, intent) != prepared
            or any(after_journals.get(name) != raw for name, raw in new_journals.items())
        ):
            raise ValueError
        if predecessor._active_binding() != (
            preparation["release_sha"],
            preparation["image"],
            preparation["config_sha"],
        ):
            raise ValueError
        phase68._source_workspace(request)
        if result.get("status") == "reconciliation_required":
            if receipt is not None or after_state != state or COMPLETED_NAME in after_journals:
                raise ValueError
            return _expected_result(after_state, result, maximum, "reconciliation_required")
        expected_status = (
            "already_submitted" if state["status"] == "adjudication_submitted" else "submitted"
        )
        result = _expected_result(after_state, result, maximum, expected_status)
        if after_state["status"] != "adjudication_submitted" or set(after_journals) != set(
            original_journals
        ) | {INTENT_NAME, COMPLETED_NAME}:
            raise ValueError
        _validate_submission_journals(after_state, after_journals, intent["request_sha256"])
        if state["status"] == "adjudication_submitted" and after_files != files:
            raise ValueError
        canonical_result = {**result, "status": "submitted"}
        expected_receipt = {
            **intent,
            "status": "receipt",
            "post_state_sha256": _sha(after_files[STATE_NAME]),
            "post_journal_set_sha256": _set_digest(after_journals),
            "result": canonical_result,
        }
        if receipt is not None and receipt != expected_receipt:
            raise ValueError
        _write_once(receipt_path, expected_receipt)
        return result
    except FirstAdjudicationSubmissionError:
        raise
    except Exception as error:
        raise FirstAdjudicationSubmissionError("adjudication evidence invalid") from error


def _require_root() -> None:
    if (
        os.name != "posix"
        or not hasattr(os, "geteuid")
        or os.geteuid() != ROOT_UID
        or os.environ.get("SUDO_USER") != host.REVIEW_USER
        or platform.system() != "Linux"
        or platform.machine() != "x86_64"
    ):
        raise FirstAdjudicationSubmissionError("invalid adjudication caller")


def main() -> int:
    try:
        _require_root()
        result = process_request(_read_request(sys.stdin.buffer))
        sys.stdout.buffer.write(contract.canonical_json(result))
        return 0
    except FirstAdjudicationSubmissionError:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
