"""Root-only coordinator for one paid final-review part-one submission."""

from __future__ import annotations

import concurrent.futures
import hashlib
import hmac
import importlib
import json
import math
import os
import platform
import re
import signal
import stat
import subprocess
import sys
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import BinaryIO, Final, Mapping

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
for _path in (_ROOT, _SCRIPTS, _ROOT / "src"):
    if os.fspath(_path) not in sys.path:
        sys.path.insert(0, os.fspath(_path))

try:
    from scripts import (  # noqa: E402
        private_speaker_review_adjudication_result_processing_contract as phase78_contract,
    )
    from scripts import private_speaker_review_final_review_host_contract as host  # noqa: E402
    from scripts import (
        private_speaker_review_final_review_submission_contract as contract,  # noqa: E402
    )
except ModuleNotFoundError:  # python -I -S when only scripts/ is available
    import private_speaker_review_adjudication_result_processing_contract as phase78_contract  # type: ignore[no-redef]
    import private_speaker_review_final_review_host_contract as host  # type: ignore[no-redef]
    import private_speaker_review_final_review_submission_contract as contract  # type: ignore[no-redef]

from cinegraph.common.speaker_review_cost_policy import (  # noqa: E402
    BATCH_COMPLETION_WINDOW,
    BATCH_DISCOUNT_MULTIPLIER,
    BATCH_ENDPOINT,
    ESTIMATED_CHARACTERS_PER_TOKEN,
    MAXIMUM_REVIEW_PART_COUNT,
    MAXIMUM_RUN_COST_USD,
    MODEL_TOKEN_PRICES,
    SPEAKER_ADJUDICATION_MODEL,
    SPEAKER_FINAL_REVIEW_MODEL,
    SPEAKER_PRIMARY_REVIEW_MODEL,
    SPEAKER_REVIEW_PROMPT_VERSION,
    SPEAKER_REVIEW_SCHEMA_VERSION,
)


class FinalReviewSubmissionError(RuntimeError):
    """Generic rejection; exception text is never returned to the caller."""


ROOT_UID: Final = 0
ROOT_GID: Final = 0
RELEASE_ROOT: Final = _ROOT
RUNS_ROOT: Final = host.SPEAKER_REVIEW_RUNS_ROOT
AUTHORIZATION_ROOT: Final = host.REVIEW_AUTHORIZATION_ROOT
RECEIPTS_ROOT: Final = host.REVIEW_FINAL_REVIEW_RECEIPTS_ROOT
PHASE78_RECEIPTS_ROOT: Path = host.REVIEW_PHASE78_RECEIPTS_ROOT
ENV_FILE: Final = host.ENV_FILE
COMPOSE_PATH: Final = RELEASE_ROOT / "deploy/compose.yaml"
WORKER_UID: Final = host.REVIEW_FINAL_REVIEW_WORKER_UID
WORKER_GID: Final = host.REVIEW_FINAL_REVIEW_WORKER_GID
STATE_NAME: Final = "run-state.json"
MAX_RECORD_BYTES: Final = 128 * 1024
MAX_FILE_BYTES: Final = 64 * 1024 * 1024
MAX_TOTAL_BYTES: Final = 256 * 1024 * 1024
ROOT_STATE_MUTABLE: Final = frozenset(
    {
        "status",
        "updated_at",
        "final_review_batch_id",
        "final_review_input_file_id",
        "final_review_batch_ids",
        "final_review_input_file_ids",
    }
)
STATE_KEYS: Final = frozenset(
    {
        "schema_version",
        "run_id",
        "status",
        "created_at",
        "updated_at",
        "candidate_count",
        "primary_model",
        "adjudication_model",
        "prompt_version",
        "maximum_cost_usd",
        "estimated_primary_cost_usd",
        "actual_primary_cost_usd",
        "actual_adjudication_cost_usd",
        "final_review_model",
        "actual_final_review_cost_usd",
        "primary_batch_id",
        "primary_input_file_id",
        "adjudication_batch_id",
        "adjudication_input_file_id",
        "primary_part_count",
        "primary_completed_part_count",
        "primary_batch_ids",
        "primary_input_file_ids",
        "adjudication_part_count",
        "adjudication_completed_part_count",
        "adjudication_batch_ids",
        "adjudication_input_file_ids",
        "final_review_part_count",
        "final_review_completed_part_count",
        "final_review_batch_ids",
        "final_review_input_file_ids",
        "final_review_batch_id",
        "final_review_input_file_id",
        "final_review_retry_count",
        "accepted_by_consensus",
        "accepted_by_adjudication",
        "accepted_by_final_review",
        "accepted_by_human",
        "needs_human",
        "actual_total_cost_usd",
    }
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_FINAL_INTENT_NAME = ".final-review-part-0001-submission-intent.json"
_FINAL_COMPLETED_NAME = ".final-review-part-0001-submission-completed.json"
_CONFIGURATION_BINDING_FILES = (
    "src/cinegraph/common/prompts.py",
    "src/cinegraph/config/models.py",
    "src/cinegraph/config/speaker_review.py",
    "src/cinegraph/common/speaker_review_cost_policy.py",
    "src/cinegraph/config/speaker_review_filesystem.py",
    "src/cinegraph/ingestion/speaker_review/batch_requests.py",
    "src/cinegraph/ingestion/speaker_review/costs.py",
)
WORKER_STATIC_ENVIRONMENT: Final = {
    "CINEGRAPH_ENVIRONMENT": "development",
    "CINEGRAPH_SPEAKER_REVIEW_RUNS_ROOT": "/review-workspace/review-runs",
    "FASTEMBED_CACHE_PATH": "/home/cinegraph/.cache/fastembed",
    "HF_HOME": "/home/cinegraph/.cache/huggingface",
    "HF_HUB_CACHE": "/home/cinegraph/.cache/huggingface/hub",
    "HF_HUB_DISABLE_PROGRESS_BARS": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_XET": "1",
    "HF_HUB_OFFLINE": "1",
    "HF_XET_CACHE": "/home/cinegraph/.cache/huggingface/xet",
    "MKL_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
}
FORBIDDEN_WORKER_ENVIRONMENT: Final = frozenset(
    {"LD_LIBRARY_PATH", "LD_PRELOAD", "OPENAI_API_KEY", "PYTHONHOME"}
)


def _canonical(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _identity(value: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
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


def _directory(path: Path, *, mode: int, owner: tuple[int, int]) -> None:
    try:
        value = path.lstat()
        if (
            not stat.S_ISDIR(value.st_mode)
            or stat.S_ISLNK(value.st_mode)
            or (os.name == "posix" and stat.S_IMODE(value.st_mode) != mode)
            or (os.name == "posix" and (value.st_uid, value.st_gid) != owner)
            or (os.name == "posix" and path.resolve(strict=True) != path)
        ):
            raise OSError
    except (OSError, RuntimeError) as error:
        raise FinalReviewSubmissionError("final-review evidence unavailable") from error


def _stable(path: Path, *, maximum: int, mode: int, owner: tuple[int, int]) -> bytes:
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
            or (os.name == "posix" and (before.st_uid, before.st_gid) != owner)
        ):
            raise OSError
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (os.name == "posix" and stat.S_IMODE(opened.st_mode) != mode)
            or (os.name == "posix" and (opened.st_uid, opened.st_gid) != owner)
        ):
            raise OSError
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = path.lstat()
        if (
            _identity(before) != _identity(opened)
            or _identity(opened) != _identity(after)
            or len(raw) != opened.st_size
        ):
            raise OSError
        return raw
    except OSError as error:
        raise FinalReviewSubmissionError("final-review evidence unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _decode(raw: bytes) -> dict[str, object]:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise FinalReviewSubmissionError("final-review record invalid") from error
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise FinalReviewSubmissionError("final-review record invalid")
    return value


def _decode_document(raw: bytes) -> dict[str, object]:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
    )
    if not isinstance(value, dict):
        raise ValueError
    return value


def _record(path: Path) -> tuple[dict[str, object], str]:
    _repair_linked_publication(path)
    raw = _stable(path, maximum=MAX_RECORD_BYTES, mode=0o600, owner=(ROOT_UID, ROOT_GID))
    return _decode(raw), _sha(raw)


_read_root_record = _record


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _repair_linked_publication(path: Path) -> None:
    pending = path.with_name(f".{path.name}.pending")
    if not os.path.lexists(pending):
        return
    try:
        published, staged = path.lstat(), pending.lstat()
        if (
            not stat.S_ISREG(published.st_mode)
            or not stat.S_ISREG(staged.st_mode)
            or stat.S_ISLNK(published.st_mode)
            or stat.S_ISLNK(staged.st_mode)
            or (published.st_dev, published.st_ino) != (staged.st_dev, staged.st_ino)
            or published.st_nlink != 2
            or staged.st_nlink != 2
            or (os.name == "posix" and stat.S_IMODE(published.st_mode) != 0o600)
            or (os.name == "posix" and (published.st_uid, published.st_gid) != (ROOT_UID, ROOT_GID))
        ):
            raise OSError
        pending.unlink()
        _fsync_directory(path.parent)
    except OSError as error:
        raise FinalReviewSubmissionError("final-review receipt conflict") from error


def _write_once(path: Path, value: Mapping[str, object]) -> str:
    encoded = _canonical(value)
    if len(encoded) > MAX_RECORD_BYTES:
        raise FinalReviewSubmissionError("final-review receipt unavailable")
    _directory(path.parent, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    pending = path.with_name(f".{path.name}.pending")
    if os.path.lexists(path):
        _repair_linked_publication(path)
        existing, digest = _record(path)
        if _canonical(existing) != encoded:
            raise FinalReviewSubmissionError("final-review receipt conflict")
        return digest
    if os.path.lexists(pending):
        raise FinalReviewSubmissionError("final-review receipt conflict")
    descriptor = -1
    try:
        descriptor = os.open(
            pending,
            os.O_CREAT
            | os.O_EXCL
            | os.O_WRONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_directory(path.parent)
        os.link(pending, path, follow_symlinks=False)
        _fsync_directory(path.parent)
        pending.unlink()
        _fsync_directory(path.parent)
    except FileExistsError as error:
        raise FinalReviewSubmissionError("final-review receipt conflict") from error
    except OSError as error:
        raise FinalReviewSubmissionError("final-review receipt unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return _sha(encoded)


def _read_request(stream: BinaryIO) -> dict[str, object]:
    raw = stream.readline(contract.REQUEST_MAX_BYTES + 1)
    if stream.read(1):
        raise FinalReviewSubmissionError("invalid final-review request")
    try:
        return contract.parse_request(raw)
    except (TypeError, ValueError) as error:
        raise FinalReviewSubmissionError("invalid final-review request") from error


def _validate_authorization(request: Mapping[str, object]) -> str:
    _directory(AUTHORIZATION_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    raw = _stable(
        AUTHORIZATION_ROOT / f"{request['authorization_id']}.json",
        maximum=contract.REQUEST_MAX_BYTES,
        mode=0o600,
        owner=(ROOT_UID, ROOT_GID),
    )
    try:
        if contract.parse_request(raw) != dict(request):
            raise ValueError
    except (TypeError, ValueError) as error:
        raise FinalReviewSubmissionError("final-review authorization invalid") from error
    return _sha(raw)


def _run_directory(request: Mapping[str, object]) -> Path:
    _directory(RUNS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    object_root, runs = RUNS_ROOT / f"sha256-{request['archive_sha256']}", None
    if object_root is not None:
        runs = object_root / "review-runs"
    _directory(object_root, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    _directory(runs, mode=0o700, owner=(WORKER_UID, WORKER_GID))
    run = runs / str(request["run_id"])
    _directory(run, mode=0o700, owner=(WORKER_UID, WORKER_GID))
    if run.resolve(strict=True).parent != runs.resolve(strict=True):
        raise FinalReviewSubmissionError("final-review run invalid")
    return run


class _Status(str, Enum):
    FINAL_REVIEW_PREPARED = "final_review_prepared"
    FINAL_REVIEW_SUBMITTED = "final_review_submitted"


@dataclass(frozen=True, slots=True)
class _State:
    values: Mapping[str, object]

    def __getattr__(self, name: str) -> object:
        try:
            value = self.values[name]
        except KeyError as error:
            raise AttributeError(name) from error
        if name == "status" and isinstance(value, str):
            return _Status(value)
        if name.endswith("_ids") and isinstance(value, list):
            return tuple(value)
        return value

    def to_dict(self) -> dict[str, object]:
        return dict(self.values)


def _state_from_raw(raw: bytes, run: Path) -> _State:
    value = _decode_document(raw)
    if (
        set(value) != STATE_KEYS
        or value.get("run_id") != run.name
        or value.get("status") not in {item.value for item in _Status}
        or value.get("schema_version") != SPEAKER_REVIEW_SCHEMA_VERSION
        or value.get("primary_model") != SPEAKER_PRIMARY_REVIEW_MODEL
        or value.get("adjudication_model") != SPEAKER_ADJUDICATION_MODEL
        or value.get("final_review_model") != SPEAKER_FINAL_REVIEW_MODEL
        or value.get("prompt_version") != SPEAKER_REVIEW_PROMPT_VERSION
        or not all(
            isinstance(value.get(key), str)
            and bool(value[key])
            and value[key] == value[key].strip()
            for key in ("created_at", "updated_at")
        )
    ):
        raise ValueError
    for key in (
        "maximum_cost_usd",
        "estimated_primary_cost_usd",
        "actual_primary_cost_usd",
        "actual_adjudication_cost_usd",
        "actual_final_review_cost_usd",
        "actual_total_cost_usd",
    ):
        if (
            type(value.get(key)) not in (int, float)
            or isinstance(value[key], bool)
            or not math.isfinite(float(value[key]))
            or float(value[key]) < 0
        ):
            raise ValueError
    total = (
        float(value["actual_primary_cost_usd"])
        + float(value["actual_adjudication_cost_usd"])
        + float(value["actual_final_review_cost_usd"])
    )
    if (
        not 0 < float(value["maximum_cost_usd"]) <= MAXIMUM_RUN_COST_USD
        or float(value["estimated_primary_cost_usd"]) > float(value["maximum_cost_usd"])
        or total > float(value["maximum_cost_usd"])
        or not math.isclose(
            float(value["actual_total_cost_usd"]), total, rel_tol=0, abs_tol=1e-9
        )
    ):
        raise ValueError
    for key in (
        "primary_part_count",
        "primary_completed_part_count",
        "adjudication_part_count",
        "adjudication_completed_part_count",
        "final_review_part_count",
        "final_review_completed_part_count",
        "final_review_retry_count",
        "candidate_count",
        "accepted_by_consensus",
        "accepted_by_adjudication",
        "accepted_by_final_review",
        "accepted_by_human",
        "needs_human",
    ):
        if type(value.get(key)) is not int or value[key] < 0:
            raise ValueError
    if (
        value["candidate_count"] <= 0
        or value["primary_part_count"] <= 0
        or any(
            value[key] > MAXIMUM_REVIEW_PART_COUNT
            for key in (
                "primary_part_count",
                "adjudication_part_count",
                "final_review_part_count",
            )
        )
    ):
        raise ValueError
    for key in (
        "primary_batch_ids",
        "primary_input_file_ids",
        "adjudication_batch_ids",
        "adjudication_input_file_ids",
        "final_review_batch_ids",
        "final_review_input_file_ids",
    ):
        if (
            not isinstance(value.get(key), list)
            or len(set(value[key])) != len(value[key])
            or not all(
                isinstance(item, str) and bool(item) and item == item.strip()
                for item in value[key]
            )
        ):
            raise ValueError
    provider_ids = (
        *value["primary_batch_ids"],
        *value["primary_input_file_ids"],
        *value["adjudication_batch_ids"],
        *value["adjudication_input_file_ids"],
        *value["final_review_batch_ids"],
        *value["final_review_input_file_ids"],
    )
    if (
        value["primary_completed_part_count"] != value["primary_part_count"]
        or value["adjudication_part_count"] <= 0
        or value["adjudication_completed_part_count"] != value["adjudication_part_count"]
        or value["final_review_part_count"] <= 0
        or value["final_review_completed_part_count"] != 0
        or len(value["primary_batch_ids"]) != value["primary_part_count"]
        or len(value["primary_input_file_ids"]) != value["primary_part_count"]
        or len(value["adjudication_batch_ids"]) != value["adjudication_part_count"]
        or len(value["adjudication_input_file_ids"]) != value["adjudication_part_count"]
        or value["primary_batch_id"] != value["primary_batch_ids"][-1]
        or value["primary_input_file_id"] != value["primary_input_file_ids"][-1]
        or value["adjudication_batch_id"] != value["adjudication_batch_ids"][-1]
        or value["adjudication_input_file_id"] != value["adjudication_input_file_ids"][-1]
        or float(value["actual_final_review_cost_usd"]) != 0.0
        or value["final_review_retry_count"] != 0
        or value["accepted_by_final_review"] != 0
        or value["accepted_by_human"] != 0
        or value["needs_human"] <= 0
        or value["final_review_part_count"] > value["needs_human"]
        or value["accepted_by_consensus"]
        + value["accepted_by_adjudication"]
        + value["needs_human"]
        != value["candidate_count"]
        or len(set(provider_ids)) != len(provider_ids)
    ):
        raise ValueError
    if value["status"] == _Status.FINAL_REVIEW_PREPARED.value:
        if (
            value["final_review_batch_id"] is not None
            or value["final_review_input_file_id"] is not None
            or value["final_review_batch_ids"]
            or value["final_review_input_file_ids"]
        ):
            raise ValueError
    elif (
        len(value["final_review_batch_ids"]) != 1
        or len(value["final_review_input_file_ids"]) != 1
        or value["final_review_batch_id"] != value["final_review_batch_ids"][0]
        or value["final_review_input_file_id"] != value["final_review_input_file_ids"][0]
    ):
        raise ValueError
    canonical = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    if canonical != raw:
        raise ValueError
    return _State(value)


def _status_value(state: object) -> str:
    value = getattr(state, "status")
    return str(getattr(value, "value", value))


def _load_state(run: Path) -> object:
    raw = _stable(run / STATE_NAME, maximum=64 * 1024, mode=0o600, owner=(WORKER_UID, WORKER_GID))
    if worker is not None:
        canonical, state = worker.load_validated_run_state(
            run, worker.DEFAULT_SPEAKER_REVIEW_CONFIGURATION
        )
        if (
            canonical != run
            or state.run_id != run.name
            or (
                json.dumps(state.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            ).encode()
            != raw
        ):
            raise ValueError
        return state
    return _state_from_raw(raw, run)


def _expected_names(state: object) -> tuple[set[str], set[str]]:
    status = getattr(getattr(state, "status"), "value", getattr(state, "status", None))
    if status not in {"final_review_prepared", "final_review_submitted"}:
        raise ValueError
    final_count, primary_count, adjudication_count = (
        int(getattr(state, "final_review_part_count")),
        int(getattr(state, "primary_part_count")),
        int(getattr(state, "adjudication_part_count")),
    )
    if min(final_count, primary_count, adjudication_count) <= 0:
        raise ValueError
    config = getattr(worker, "DEFAULT_SPEAKER_REVIEW_CONFIGURATION", None)
    final_filename = getattr(config, "final_decisions_filename", "final-decisions.jsonl")
    required = {
        STATE_NAME,
        "candidates.jsonl",
        "source-manifest.json",
        "primary-verdicts.jsonl",
        "primary-parse-errors.json",
        "primary-decisions.jsonl",
        "adjudication-verdicts.jsonl",
        "adjudication-parse-errors.json",
        final_filename,
        *(f"primary-part-{i:04d}-requests.jsonl" for i in range(1, primary_count + 1)),
        *(f"primary-part-{i:04d}-output.jsonl" for i in range(1, primary_count + 1)),
        *(f"adjudication-part-{i:04d}-requests.jsonl" for i in range(1, adjudication_count + 1)),
        *(f"adjudication-part-{i:04d}-output.jsonl" for i in range(1, adjudication_count + 1)),
        *(f"final-review-part-{i:04d}-requests.jsonl" for i in range(1, final_count + 1)),
        *(
            f".{stage}-part-{i:04d}-submission-{kind}.json"
            for stage, count in (("primary", primary_count), ("adjudication", adjudication_count))
            for i in range(1, count + 1)
            for kind in ("intent", "completed")
        ),
    }
    optional = {
        *(f"primary-part-{i:04d}-api-errors.jsonl" for i in range(1, primary_count + 1)),
        *(f"adjudication-part-{i:04d}-api-errors.jsonl" for i in range(1, adjudication_count + 1)),
    }
    if status == "final_review_prepared":
        optional.update({_FINAL_INTENT_NAME, _FINAL_COMPLETED_NAME})
    else:
        required.update({_FINAL_INTENT_NAME, _FINAL_COMPLETED_NAME})
    return required, optional


def _inventory(run: Path) -> tuple[dict[str, bytes], object]:
    try:
        state = _load_state(run)
        names = {entry.name for entry in run.iterdir()}
        required, optional = _expected_names(state)
        if not required <= names or not names <= required | optional:
            raise ValueError
        contents: dict[str, bytes] = {}
        total = 0
        for name in sorted(names):
            raw = _stable(
                run / name,
                maximum=64 * 1024 if name == STATE_NAME else MAX_FILE_BYTES,
                mode=0o600,
                owner=(WORKER_UID, WORKER_GID),
            )
            total += len(raw)
            if total > MAX_TOTAL_BYTES:
                raise ValueError
            contents[name] = raw
        if contents[STATE_NAME] != _stable(
            run / STATE_NAME, maximum=64 * 1024, mode=0o600, owner=(WORKER_UID, WORKER_GID)
        ):
            raise ValueError
        return contents, state
    except Exception as error:
        raise FinalReviewSubmissionError("final-review inventory invalid") from error


def _classes(contents: Mapping[str, bytes]) -> tuple[dict[str, bytes], ...]:
    artifacts: dict[str, bytes] = {}
    journals: dict[str, bytes] = {}
    outputs: dict[str, bytes] = {}
    derived: dict[str, bytes] = {}
    for name, raw in contents.items():
        if name == STATE_NAME:
            continue
        if name.startswith(".") and "-submission-" in name:
            journals[name] = raw
        elif name.endswith("-output.jsonl") or name.endswith("-api-errors.jsonl"):
            outputs[name] = raw
        elif name.endswith("-requests.jsonl") or name in {
            "candidates.jsonl",
            "source-manifest.json",
        }:
            artifacts[name] = raw
        else:
            derived[name] = raw
    return artifacts, journals, outputs, derived


def _set_digest(contents: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(contents):
        encoded = name.encode()
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(contents[name]).to_bytes(8, "big"))
        digest.update(contents[name])
    return digest.hexdigest()


def _digests(contents: Mapping[str, bytes]) -> dict[str, str]:
    artifacts, journals, outputs, derived = _classes(contents)
    return {
        "state": _sha(contents[STATE_NAME]),
        "artifacts": _set_digest(artifacts),
        "journals": _set_digest(journals),
        "outputs": _set_digest(outputs),
        "derived": _set_digest(derived),
    }


def _hashes(contents: Mapping[str, bytes]) -> dict[str, dict[str, str]]:
    groups = _classes(contents)
    return {
        name: {key: _sha(raw) for key, raw in sorted(group.items())}
        for name, group in zip(("artifacts", "journals", "outputs", "derived"), groups)
    }


def _phase78_groups(contents: Mapping[str, bytes]) -> dict[str, dict[str, bytes]]:
    """Phase 78's disjoint inventory classes (requests are separate)."""
    artifacts: dict[str, bytes] = {}
    requests: dict[str, bytes] = {}
    journals: dict[str, bytes] = {}
    outputs: dict[str, bytes] = {}
    derived: dict[str, bytes] = {}
    for name, raw in contents.items():
        if name == STATE_NAME:
            continue
        if name.startswith(".") and "-submission-" in name:
            journals[name] = raw
        elif name.endswith("-requests.jsonl"):
            requests[name] = raw
        elif name.endswith("-output.jsonl") or name.endswith("-api-errors.jsonl"):
            outputs[name] = raw
        elif name in {"candidates.jsonl", "source-manifest.json"}:
            artifacts[name] = raw
        else:
            derived[name] = raw
    return {
        "artifacts": artifacts,
        "requests": requests,
        "journals": journals,
        "outputs": outputs,
        "derived": derived,
    }


def _phase78_digests(contents: Mapping[str, bytes]) -> dict[str, str]:
    groups = _phase78_groups(contents)
    return {
        "state": _sha(contents[STATE_NAME]),
        **{name: _set_digest(group) for name, group in groups.items()},
    }


def _phase78_hashes(contents: Mapping[str, bytes]) -> dict[str, dict[str, str]]:
    return {
        name: {key: _sha(raw) for key, raw in sorted(group.items())}
        for name, group in _phase78_groups(contents).items()
    }


def _pre_submission_contents(contents: Mapping[str, bytes]) -> dict[str, bytes]:
    """Remove only final-review journals from the prepared pre-image.

    The worker intentionally binds a prepared checkpoint before its two
    application journals exist.  Recovery must authenticate that original
    image while still validating those journals independently.
    """
    return {
        name: raw
        for name, raw in contents.items()
        if name not in {_FINAL_INTENT_NAME, _FINAL_COMPLETED_NAME}
    }


def _validate_phase78_predecessor(
    request: Mapping[str, object],
    run: Path,
    state: object,
    contents: Mapping[str, bytes] | None = None,
) -> tuple[str, str]:
    try:
        intent, intent_sha = _record(PHASE78_RECEIPTS_ROOT / f"{request['run_id']}.intent.json")
        receipt, receipt_sha = _record(PHASE78_RECEIPTS_ROOT / f"{request['run_id']}.json")
        intent_keys = {
            "archive_sha256",
            "authorization_id",
            "authorization_sha256",
            "configuration_sha256",
            "fourth_observation_intent_sha256",
            "fourth_observation_receipt_sha256",
            "image_reference",
            "maximum_authorized_cost_microusd",
            "operation",
            "pre_digests",
            "pre_directory_identities",
            "pre_hashes",
            "pre_state_binding_sha256",
            "pre_state_sha256",
            "preparation_receipt_sha256",
            "purpose",
            "release_sha",
            "request_sha256",
            "run_id",
            "schema_version",
            "season_number",
            "source_manifest_sha256",
            "status",
        }
        receipt_keys = {
            "aggregate",
            "archive_sha256",
            "authorization_claim_sha256",
            "authorization_id",
            "intent_sha256",
            "operation",
            "post_counts",
            "post_digests",
            "purpose",
            "run_id",
            "schema_version",
            "season_number",
            "status",
        }
        if (
            set(intent) != intent_keys
            or set(receipt) != receipt_keys
            or intent.get("run_id") != request["run_id"]
            or intent.get("archive_sha256") != request["archive_sha256"]
            or intent.get("operation") != phase78_contract.OPERATION
            or intent.get("purpose") != phase78_contract.PURPOSE
            or intent.get("schema_version") != phase78_contract.PROTOCOL_VERSION
            or intent.get("season_number") != phase78_contract.SEASON_NUMBER
            or intent.get("status") != "intent"
            or receipt.get("intent_sha256") != intent_sha
            or receipt.get("status") != "receipt"
            or receipt.get("archive_sha256") != request["archive_sha256"]
            or receipt.get("authorization_id") != intent.get("authorization_id")
            or receipt.get("operation") != phase78_contract.OPERATION
            or receipt.get("purpose") != phase78_contract.PURPOSE
            or receipt.get("run_id") != request["run_id"]
            or receipt.get("schema_version") != phase78_contract.PROTOCOL_VERSION
            or receipt.get("season_number") != phase78_contract.SEASON_NUMBER
        ):
            raise ValueError
        phase78_request = phase78_contract.validate_request(
            {
                "archive_sha256": request["archive_sha256"],
                "authorization_id": intent["authorization_id"],
                "maximum_authorized_cost_microusd": intent[
                    "maximum_authorized_cost_microusd"
                ],
                "operation": phase78_contract.OPERATION,
                "purpose": phase78_contract.PURPOSE,
                "run_id": request["run_id"],
                "schema_version": phase78_contract.PROTOCOL_VERSION,
                "season_number": phase78_contract.SEASON_NUMBER,
            }
        )
        phase78_authorization = _stable(
            AUTHORIZATION_ROOT / f"{intent['authorization_id']}.json",
            maximum=phase78_contract.REQUEST_MAX_BYTES,
            mode=0o600,
            owner=(ROOT_UID, ROOT_GID),
        )
        if (
            phase78_contract.parse_request(phase78_authorization) != phase78_request
            or intent.get("authorization_sha256") != _sha(phase78_authorization)
            or intent.get("request_sha256")
            != _sha(phase78_contract.canonical_json(phase78_request))
        ):
            raise ValueError
        phase78_claim, phase78_claim_sha = _record(
            PHASE78_RECEIPTS_ROOT / f"authorization-{intent['authorization_id']}.claim.json"
        )
        expected_claim = {
            "archive_sha256": phase78_request["archive_sha256"],
            "authorization_id": phase78_request["authorization_id"],
            "authorization_sha256": _sha(phase78_authorization),
            "maximum_authorized_cost_microusd": phase78_request[
                "maximum_authorized_cost_microusd"
            ],
            "operation": phase78_contract.OPERATION,
            "request_sha256": _sha(phase78_contract.canonical_json(phase78_request)),
            "run_id": phase78_request["run_id"],
            "schema_version": phase78_contract.PROTOCOL_VERSION,
            "status": "claimed",
        }
        if (
            phase78_claim != expected_claim
            or receipt.get("authorization_claim_sha256") != phase78_claim_sha
        ):
            raise ValueError
        if contents is None:
            contents, _ = _inventory(run)
        baseline = _pre_submission_contents(contents)
        current = _phase78_digests(baseline)
        if _status_value(state) == _Status.FINAL_REVIEW_SUBMITTED.value:
            stored_phase79_intent, _ = _record(RECEIPTS_ROOT / f"{request['run_id']}.intent.json")
            pre_state_sha = stored_phase79_intent.get("pre_run_state_sha256")
            if not _is_sha(pre_state_sha):
                raise ValueError
            current["state"] = pre_state_sha
        groups = _phase78_groups(baseline)
        post = receipt.get("post_digests")
        if (
            not isinstance(post, dict)
            or post != current
            or receipt.get("post_counts")
            != {name: len(group) for name, group in groups.items()}
        ):
            raise ValueError
        pre = intent.get("pre_digests")
        pre_hashes = intent.get("pre_hashes")
        if (
            not isinstance(pre, dict)
            or set(pre) != {"state", "artifacts", "requests", "journals", "outputs", "derived"}
            or not all(_is_sha(value) for value in pre.values())
            or not isinstance(pre_hashes, dict)
            or set(pre_hashes) != {"artifacts", "requests", "journals", "outputs", "derived"}
            or not all(
                isinstance(group, dict)
                and all(isinstance(name, str) and _is_sha(value) for name, value in group.items())
                for group in pre_hashes.values()
            )
            or intent.get("source_manifest_sha256")
            != _sha(baseline["source-manifest.json"])
        ):
            raise ValueError
        for key in (
            "authorization_sha256",
            "fourth_observation_intent_sha256",
            "fourth_observation_receipt_sha256",
            "pre_state_binding_sha256",
            "pre_state_sha256",
            "preparation_receipt_sha256",
            "request_sha256",
        ):
            if not _is_sha(intent.get(key)):
                raise ValueError
        if not _is_sha(receipt.get("authorization_claim_sha256")):
            raise ValueError
        for key in ("release_sha", "configuration_sha256", "image_reference", "request_sha256"):
            if not isinstance(intent.get(key), str) or not intent[key]:
                raise ValueError
        aggregate = receipt.get("aggregate")
        expected_aggregate = {
            "accepted_by_consensus": getattr(state, "accepted_by_consensus"),
            "accepted_by_adjudication": getattr(state, "accepted_by_adjudication"),
            "actual_adjudication_cost_microusd": _cost_micros(
                getattr(state, "actual_adjudication_cost_usd")
            ),
            "actual_primary_cost_microusd": _cost_micros(
                getattr(state, "actual_primary_cost_usd")
            ),
            "adjudication_completed_part_count": getattr(
                state, "adjudication_completed_part_count"
            ),
            "adjudication_part_count": getattr(state, "adjudication_part_count"),
            "candidate_count": getattr(state, "candidate_count"),
            "final_review_part_count": getattr(state, "final_review_part_count"),
            "maximum_authorized_cost_microusd": intent[
                "maximum_authorized_cost_microusd"
            ],
            "needs_human": getattr(state, "needs_human"),
            "operation": phase78_contract.OPERATION,
            "primary_completed_part_count": getattr(state, "primary_completed_part_count"),
            "primary_part_count": getattr(state, "primary_part_count"),
            "purpose": phase78_contract.PURPOSE,
            "run_status": "final_review_prepared",
            "season_number": phase78_contract.SEASON_NUMBER,
            "status": "final_review_prepared",
        }
        if aggregate != phase78_contract.validate_aggregate(
            expected_aggregate, status="final_review_prepared"
        ):
            raise ValueError
        return intent_sha, receipt_sha
    except Exception as error:
        raise FinalReviewSubmissionError("final-review predecessor invalid") from error


def _configuration_sha256(release: Path = RELEASE_ROOT) -> str:
    digest = hashlib.sha256()
    for locator in _CONFIGURATION_BINDING_FILES:
        path = release.joinpath(*Path(locator).parts)
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > MAX_RECORD_BYTES
        ):
            raise FinalReviewSubmissionError("configuration unavailable")
        raw = _stable(
            path,
            maximum=MAX_RECORD_BYTES,
            mode=stat.S_IMODE(metadata.st_mode),
            owner=(metadata.st_uid, metadata.st_gid),
        )
        encoded = locator.encode("ascii")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return digest.hexdigest()


def _environment_values() -> dict[str, str]:
    raw = _stable(ENV_FILE, maximum=64 * 1024, mode=0o600, owner=(ROOT_UID, ROOT_GID))
    values: dict[str, str] = {}
    for line in raw.decode().splitlines():
        if not line or line.startswith("#"):
            continue
        key, sep, val = line.partition("=")
        if not sep or not key or key in values:
            raise FinalReviewSubmissionError("active runtime unavailable")
        values[key] = val
    return values


def _active_runtime_binding() -> tuple[str, str, str]:
    release_sha = RELEASE_ROOT.name
    if _HEX40.fullmatch(release_sha) is None:
        raise FinalReviewSubmissionError("active release unavailable")
    values = _environment_values()
    image, image_digest = (
        values.get("CINEGRAPH_IMAGE", ""),
        values.get("CINEGRAPH_IMAGE_DIGEST", ""),
    )
    if image != "ghcr.io/captainvc/cinegraph" or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", image_digest
    ):
        raise FinalReviewSubmissionError("active runtime unavailable")
    image_reference = f"{image}@{image_digest}"
    try:
        inspected = subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                image_reference,
                "--format",
                "{{json .Config.Labels}}",
            ],
            cwd=RELEASE_ROOT,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
            timeout=host.REVIEW_FINAL_REVIEW_KILL_AFTER_SECONDS,
        )
        labels = _decode_document(inspected.stdout)
        if (
            inspected.returncode != 0
            or inspected.stderr
            or len(inspected.stdout) > contract.OUTPUT_MAX_BYTES
            or labels.get(host.CINEGRAPH_IMAGE_REVISION_LABEL) != release_sha
            or labels.get(host.CINEGRAPH_IMAGE_SOURCE_LABEL) != host.CINEGRAPH_IMAGE_SOURCE
            or labels.get(host.CINEGRAPH_IMAGE_VERSION_LABEL) != f"sha-{release_sha}"
        ):
            raise ValueError
    except (
        OSError,
        subprocess.SubprocessError,
        TimeoutError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
    ) as error:
        raise FinalReviewSubmissionError("active runtime unavailable") from error
    return release_sha, image_reference, _configuration_sha256()


def _secret_source_is_exact(mount: Mapping[str, object]) -> bool:
    try:
        source = mount.get("Source")
        secret = _environment_values().get("OPENAI_API_KEY", "")
        if (
            not isinstance(source, str)
            or not secret
            or secret != secret.strip()
            or "\x00" in secret
        ):
            return False
        path = Path(source)
        if not path.is_absolute():
            return False
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_nlink != 1
            or (os.name == "posix" and metadata.st_uid != ROOT_UID)
            or (os.name == "posix" and metadata.st_gid != ROOT_GID)
            or (os.name == "posix" and mode & 0o022)
            or (os.name == "posix" and path.resolve(strict=True) != path)
        ):
            return False
        raw = _stable(path, maximum=4_096, mode=mode, owner=(ROOT_UID, ROOT_GID))
        return hmac.compare_digest(raw, secret.encode("utf-8"))
    except (OSError, UnicodeError, FinalReviewSubmissionError):
        return False


def _runtime_parameters(state: object) -> dict[str, str]:
    values = {
        "final_review_model": str(getattr(state, "final_review_model")),
        "prompt_version": str(getattr(state, "prompt_version")),
        "batch_endpoint": BATCH_ENDPOINT,
        "completion_window": BATCH_COMPLETION_WINDOW,
    }
    if (
        any(not value for value in values.values())
        or values["final_review_model"] != SPEAKER_FINAL_REVIEW_MODEL
    ):
        raise FinalReviewSubmissionError("final-review runtime binding unavailable")
    return values


def _cost_micros(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise FinalReviewSubmissionError("final-review cost invalid")
    try:
        result = int(
            (Decimal(str(value)) * Decimal(1_000_000)).to_integral_value(rounding=ROUND_CEILING)
        )
    except (InvalidOperation, ArithmeticError, ValueError) as error:
        raise FinalReviewSubmissionError("final-review cost invalid") from error
    if result > contract.MAXIMUM_AUTHORIZED_COST_MICROUSD:
        raise FinalReviewSubmissionError("final-review cost invalid")
    return result


def _estimate_final_review_cost(contents: Mapping[str, bytes], state: object) -> int:
    try:
        model = str(getattr(state, "final_review_model"))
        part_count = getattr(state, "final_review_part_count")
        if (
            model != SPEAKER_FINAL_REVIEW_MODEL
            or model not in MODEL_TOKEN_PRICES
            or type(part_count) is not int
            or part_count <= 0
        ):
            raise ValueError

        def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError
                result[key] = value
            return result

        requests: list[dict[str, object]] = []
        for part in range(1, part_count + 1):
            raw = contents[f"final-review-part-{part:04d}-requests.jsonl"]
            if not raw or not raw.endswith(b"\n"):
                raise ValueError
            for line in raw.splitlines():
                request = json.loads(
                    line.decode("utf-8"),
                    object_pairs_hook=unique,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                )
                if not isinstance(request, dict) or not isinstance(request.get("body"), dict):
                    raise ValueError
                maximum = request["body"].get("max_output_tokens")
                if type(maximum) is not int or maximum <= 0:
                    raise ValueError
                requests.append(request)
        if not requests:
            raise ValueError
        input_characters = sum(
            len(json.dumps(request["body"], ensure_ascii=False, separators=(",", ":")))
            for request in requests
        )
        input_tokens = math.ceil(input_characters / ESTIMATED_CHARACTERS_PER_TOKEN)
        output_tokens = sum(int(request["body"]["max_output_tokens"]) for request in requests)
        input_price, output_price = MODEL_TOKEN_PRICES[model]
        estimate = (
            (input_tokens * input_price + output_tokens * output_price)
            / 1_000_000
            * BATCH_DISCOUNT_MULTIPLIER
        )
        return _cost_micros(estimate)
    except Exception as error:
        raise FinalReviewSubmissionError("final-review cost invalid") from error


def _validate_budget(
    request: Mapping[str, object], state: object, estimated: int
) -> tuple[int, int]:
    cap = int(request["maximum_authorized_cost_microusd"])
    maximum = _cost_micros(getattr(state, "maximum_cost_usd"))
    actual = _cost_micros(getattr(state, "actual_primary_cost_usd")) + _cost_micros(
        getattr(state, "actual_adjudication_cost_usd")
    )
    if actual + estimated > cap or actual + estimated > maximum:
        raise FinalReviewSubmissionError("final-review cost exceeds authorization")
    return actual, maximum


def _worker_args(
    request: Mapping[str, object], run: Path, bindings: Mapping[str, str]
) -> list[str]:
    args = [
        "docker",
        "compose",
        "--progress",
        "quiet",
        "--env-file",
        os.fspath(ENV_FILE),
        "--profile",
        host.REVIEW_FINAL_REVIEW_COMPOSE_PROFILE,
        "-f",
        os.fspath(COMPOSE_PATH),
        "run",
        "--rm",
        "--no-deps",
        "--no-TTY",
        "--pull",
        "never",
        "--name",
        host.REVIEW_FINAL_REVIEW_CONTAINER_NAME,
        "--user",
        f"{WORKER_UID}:{WORKER_GID}",
    ]
    environment = _worker_environment(request, bindings)
    for name, value in sorted(environment.items()):
        args.extend(("--env", f"{name}={value}"))
    args.extend(
        (
            "--volume",
            f"{run.as_posix()}:{(host.REVIEW_FINAL_REVIEW_RUNS_TARGET / str(request['run_id'])).as_posix()}:rw",
            host.REVIEW_FINAL_REVIEW_COMPOSE_SERVICE,
        )
    )
    return args


def _safe_env() -> dict[str, str]:
    return {
        "PATH": "/usr/sbin:/usr/bin",
        "HOME": "/root",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _worker_environment(
    request: Mapping[str, object], bindings: Mapping[str, str]
) -> dict[str, str]:
    return {
        contract.ENV_ARCHIVE_SHA256: str(request["archive_sha256"]),
        contract.ENV_RUN_ID: str(request["run_id"]),
        contract.ENV_AUTHORIZATION_ID: str(request["authorization_id"]),
        contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: str(
            request["maximum_authorized_cost_microusd"]
        ),
        **dict(bindings),
    }


def _environment_map(value: object, *, allow_none: bool = False) -> dict[str, str] | None:
    if value is None and allow_none:
        return {}
    if not isinstance(value, list):
        return None
    result: dict[str, str] = {}
    for item in value:
        if not isinstance(item, str) or "=" not in item:
            return None
        name, item_value = item.split("=", 1)
        if not name or name in result:
            return None
        result[name] = item_value
    return result


def _container_identity_is_exact(
    request: Mapping[str, object], run_parent: Path, bindings: Mapping[str, str]
) -> bool:
    try:
        image_result = subprocess.run(
            [
                "docker",
                "compose",
                "--progress",
                "quiet",
                "--env-file",
                os.fspath(ENV_FILE),
                "--profile",
                host.REVIEW_FINAL_REVIEW_COMPOSE_PROFILE,
                "-f",
                os.fspath(COMPOSE_PATH),
                "config",
                "--images",
                host.REVIEW_FINAL_REVIEW_COMPOSE_SERVICE,
            ],
            cwd=RELEASE_ROOT,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=host.REVIEW_FINAL_REVIEW_KILL_AFTER_SECONDS,
        )
        image = image_result.stdout.decode().strip()
        image_inspected = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{json .Config.Env}}", image],
            cwd=RELEASE_ROOT,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=host.REVIEW_FINAL_REVIEW_KILL_AFTER_SECONDS,
        )
        inspected = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .}}",
                host.REVIEW_FINAL_REVIEW_CONTAINER_NAME,
            ],
            cwd=RELEASE_ROOT,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=host.REVIEW_FINAL_REVIEW_KILL_AFTER_SECONDS,
        )
        value = json.loads(inspected.stdout.decode())
        config, host_config = value["Config"], value["HostConfig"]
        mounts = value.get("Mounts", [])
        labels = config.get("Labels", {})
        expected_source, expected_destination = (
            (run_parent / str(request["run_id"])).as_posix(),
            (host.REVIEW_FINAL_REVIEW_RUNS_TARGET / str(request["run_id"])).as_posix(),
        )
        if not isinstance(mounts, list) or not all(isinstance(mount, dict) for mount in mounts):
            return False
        destinations = {mount.get("Destination"): mount for mount in mounts}
        if len(destinations) != len(mounts):
            return False
        run_mount, secret_mount, tmp_mount = (
            destinations.get(expected_destination, {}),
            destinations.get(host.REVIEW_FINAL_REVIEW_SECRET_TARGET, {}),
            destinations.get(host.REVIEW_FINAL_REVIEW_TMP_TARGET, {}),
        )
        image_environment = _environment_map(
            json.loads(image_inspected.stdout.decode()), allow_none=True
        )
        actual_env = _environment_map(config.get("Env"))
        expected_env = (
            None
            if image_environment is None
            else {
                **image_environment,
                **WORKER_STATIC_ENVIRONMENT,
                **_worker_environment(request, bindings),
            }
        )
        networks = value.get("NetworkSettings", {}).get("Networks", {})
        return (
            image_result.returncode == 0
            and image_inspected.returncode == 0
            and inspected.returncode == 0
            and bool(image)
            and value.get("Name") == f"/{host.REVIEW_FINAL_REVIEW_CONTAINER_NAME}"
            and config.get("Image") == image
            and config.get("User") == f"{WORKER_UID}:{WORKER_GID}"
            and config.get("WorkingDir") == host.REVIEW_FINAL_REVIEW_CONTAINER_WORKDIR
            and config.get("Cmd") == list(host.REVIEW_FINAL_REVIEW_CONTAINER_COMMAND)
            and actual_env == expected_env
            and actual_env is not None
            and not FORBIDDEN_WORKER_ENVIRONMENT.intersection(actual_env)
            and labels.get("com.docker.compose.project") == host.REVIEW_FINAL_REVIEW_COMPOSE_PROJECT
            and labels.get("com.docker.compose.service") == host.REVIEW_FINAL_REVIEW_COMPOSE_SERVICE
            and labels.get("com.docker.compose.oneoff") == "True"
            and labels.get("com.docker.compose.project.config_files") == os.fspath(COMPOSE_PATH)
            and labels.get("com.docker.compose.project.working_dir") == os.fspath(RELEASE_ROOT)
            and host_config.get("ReadonlyRootfs") is True
            and host_config.get("Privileged") is False
            and host_config.get("CapDrop") == ["ALL"]
            and "no-new-privileges:true" in (host_config.get("SecurityOpt") or [])
            and host_config.get("PidsLimit") == 128
            and isinstance(host_config.get("Memory"), int)
            and 67_108_864 <= host_config["Memory"] <= 4_294_967_296
            and isinstance(host_config.get("NanoCpus"), int)
            and 100_000_000 <= host_config["NanoCpus"] <= 4_000_000_000
            and isinstance(networks, dict)
            and set(networks) == {host.REVIEW_FINAL_REVIEW_NETWORK}
            and run_mount.get("Type") == "bind"
            and run_mount.get("Source") == expected_source
            and run_mount.get("Mode") == "rw"
            and run_mount.get("Propagation") == "rprivate"
            and run_mount.get("RW") is True
            and secret_mount.get("Destination") == host.REVIEW_FINAL_REVIEW_SECRET_TARGET
            and secret_mount.get("Type") == "bind"
            and secret_mount.get("Mode") == "ro"
            and secret_mount.get("Propagation") == "rprivate"
            and secret_mount.get("RW") is False
            and _secret_source_is_exact(secret_mount)
            and tmp_mount.get("Destination") == host.REVIEW_FINAL_REVIEW_TMP_TARGET
            and tmp_mount.get("Type") == "tmpfs"
            and len(destinations) == 3
        )
    except (
        OSError,
        subprocess.SubprocessError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        AttributeError,
        ValueError,
    ):
        return False


def _cleanup_worker(
    request: Mapping[str, object], run_parent: Path, bindings: Mapping[str, str]
) -> None:
    if not _container_identity_is_exact(request, run_parent, bindings):
        return
    try:
        subprocess.run(
            ["docker", "rm", "--force", host.REVIEW_FINAL_REVIEW_CONTAINER_NAME],
            cwd=RELEASE_ROOT,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=host.REVIEW_FINAL_REVIEW_KILL_AFTER_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return


def _read_bounded(stream: BinaryIO) -> bytes:
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


def _terminate(process: subprocess.Popen[bytes]) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=host.REVIEW_FINAL_REVIEW_KILL_AFTER_SECONDS)
        else:
            process.kill()
            process.wait()
    except (OSError, ProcessLookupError, subprocess.SubprocessError):
        try:
            process.kill()
            process.wait()
        except (OSError, subprocess.SubprocessError):
            pass


def _run_worker(
    request: Mapping[str, object], run: Path, bindings: Mapping[str, str]
) -> dict[str, object]:
    process: subprocess.Popen[bytes] | None = None
    try:
        _cleanup_worker(request, run.parent, bindings)
        process = subprocess.Popen(
            _worker_args(request, run, bindings),
            cwd=RELEASE_ROOT,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=(os.name == "posix"),
        )
        if process.stdout is None or process.stderr is None:
            raise OSError
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            stdout_future, stderr_future = (
                pool.submit(_read_bounded, process.stdout),
                pool.submit(_read_bounded, process.stderr),
            )
            try:
                code = process.wait(timeout=host.REVIEW_FINAL_REVIEW_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as error:
                _terminate(process)
                stdout_future.result(timeout=5)
                stderr_future.result(timeout=5)
                raise FinalReviewSubmissionError("final-review worker failed") from error
            output, error_output = stdout_future.result(timeout=5), stderr_future.result(timeout=5)
        if code != 0 or error_output or len(output) > contract.OUTPUT_MAX_BYTES:
            raise OSError
        return contract.parse_aggregate(output)
    except FinalReviewSubmissionError:
        raise
    except (OSError, subprocess.SubprocessError, TimeoutError, ValueError) as error:
        raise FinalReviewSubmissionError("final-review worker failed") from error
    finally:
        if process is not None and process.poll() is None:
            _terminate(process)
        if process is not None:
            _cleanup_worker(request, run.parent, bindings)


def _application_record(raw: bytes, *, completed: bool) -> dict[str, object]:
    value = _decode_document(raw)
    required = (
        {"binding", "batch_id", "input_file_id", "status"}
        if completed
        else {"binding", "status"}
    )
    binding = value.get("binding")
    if (
        set(value) != required
        or _canonical(value) != raw
        or not isinstance(binding, dict)
        or (not completed and value.get("status") != "intent")
        or (
            completed
            and not all(
                isinstance(value.get(key), str)
                and bool(value[key])
                and value[key].strip() == value[key]
                for key in ("batch_id", "input_file_id", "status")
            )
        )
    ):
        raise ValueError
    return value


def _validate_application_journals(
    contents: Mapping[str, bytes], request: Mapping[str, object], state: object, request_sha: str
) -> tuple[bool, bool]:
    intent_present, completed_present = (
        _FINAL_INTENT_NAME in contents,
        _FINAL_COMPLETED_NAME in contents,
    )
    if not intent_present and not completed_present:
        return False, False
    params = _runtime_parameters(state)
    expected = {
        "schema_version": 1,
        "request_sha256": request_sha,
        "run_id": request["run_id"],
        "stage": "final-review",
        "part": 1,
        "prompt_version": getattr(state, "prompt_version"),
        "batch_endpoint": params["batch_endpoint"],
        "completion_window": params["completion_window"],
    }
    try:
        intent = (
            _application_record(contents[_FINAL_INTENT_NAME], completed=False)
            if intent_present
            else None
        )
        completed = (
            _application_record(contents[_FINAL_COMPLETED_NAME], completed=True)
            if completed_present
            else None
        )
    except (KeyError, TypeError, ValueError) as error:
        raise FinalReviewSubmissionError("final-review application journal invalid") from error
    if intent is None or completed is None:
        present = intent if intent is not None else completed
        if present is None or present["binding"] != expected:
            raise FinalReviewSubmissionError("final-review application journal invalid")
        return intent_present, completed_present
    if (
        intent["binding"] != expected
        or completed["binding"] != expected
    ):
        raise FinalReviewSubmissionError("final-review application journal invalid")
    if _status_value(state) == _Status.FINAL_REVIEW_SUBMITTED.value:
        ids = getattr(state, "final_review_batch_ids")
        inputs = getattr(state, "final_review_input_file_ids")
        if (
            tuple(ids) != (completed["batch_id"],)
            or tuple(inputs) != (completed["input_file_id"],)
            or getattr(state, "final_review_batch_id") != completed["batch_id"]
            or getattr(state, "final_review_input_file_id") != completed["input_file_id"]
        ):
            raise FinalReviewSubmissionError("final-review application journal invalid")
    return True, True


def _aggregate(
    request: Mapping[str, object], state: object, *, status: str, estimated: int, submitted: int
) -> dict[str, object]:
    try:
        value = {
            "actual_primary_cost_microusd": _cost_micros(getattr(state, "actual_primary_cost_usd")),
            "actual_adjudication_cost_microusd": _cost_micros(
                getattr(state, "actual_adjudication_cost_usd")
            ),
            "estimated_final_review_cost_microusd": estimated,
            "final_review_completed_part_count": getattr(
                state, "final_review_completed_part_count"
            ),
            "final_review_part_count": getattr(state, "final_review_part_count"),
            "operation": contract.OPERATION,
            "purpose": contract.PURPOSE,
            "run_id": request["run_id"],
            "run_status": getattr(getattr(state, "status"), "value", getattr(state, "status")),
            "season_number": contract.SEASON_NUMBER,
            "status": status,
            "submitted_part_count": submitted,
        }
        return contract.validate_aggregate(value, status=status)
    except (TypeError, ValueError, KeyError) as error:
        raise FinalReviewSubmissionError("final-review aggregate invalid") from error


def _claim_payload(request: Mapping[str, object], authorization_sha: str) -> dict[str, object]:
    return {
        "archive_sha256": request["archive_sha256"],
        "authorization_id": request["authorization_id"],
        "authorization_sha256": authorization_sha,
        "maximum_authorized_cost_microusd": request["maximum_authorized_cost_microusd"],
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "request_sha256": _sha(_canonical(request)),
        "run_id": request["run_id"],
        "schema_version": contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
        "status": "claimed",
    }


def _intent_payload(
    request: Mapping[str, object],
    *,
    auth_sha: str,
    claim_sha: str,
    predecessor: tuple[str, str],
    contents: Mapping[str, bytes],
    state: object,
    estimated: int,
) -> dict[str, object]:
    release_sha, image_reference, config_sha = _active_runtime_binding()
    params = _runtime_parameters(state)
    digests, hashes = _digests(contents), _hashes(contents)
    return {
        "archive_sha256": request["archive_sha256"],
        "authorization_id": request["authorization_id"],
        "authorization_sha256": auth_sha,
        "authorization_claim_sha256": claim_sha,
        "maximum_authorized_cost_microusd": request["maximum_authorized_cost_microusd"],
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": request["run_id"],
        "schema_version": contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
        "status": "intent",
        "request_sha256": _sha(_canonical(request)),
        "release_sha": release_sha,
        "image_reference": image_reference,
        "configuration_sha256": config_sha,
        **params,
        "phase78_processing_intent_sha256": predecessor[0],
        "phase78_processing_receipt_sha256": predecessor[1],
        "pre_digests": digests,
        "pre_hashes": hashes,
        "pre_run_state_sha256": digests["state"],
        "pre_artifact_set_sha256": digests["artifacts"],
        "pre_journal_set_sha256": digests["journals"],
        "pre_output_set_sha256": digests["outputs"],
        "pre_derived_set_sha256": digests["derived"],
        "part_one_request_sha256": _sha(contents["final-review-part-0001-requests.jsonl"]),
        "estimated_final_review_cost_microusd": estimated,
        "prior_actual_cost_microusd": _cost_micros(getattr(state, "actual_primary_cost_usd"))
        + _cost_micros(getattr(state, "actual_adjudication_cost_usd")),
        "state_maximum_cost_microusd": _cost_micros(getattr(state, "maximum_cost_usd")),
    }


def _validate_intent(
    value: object,
    expected: Mapping[str, object],
    *,
    contents: Mapping[str, bytes],
    state: object,
) -> dict[str, object]:
    if (
        not isinstance(value, dict)
        or set(value) != set(expected)
        or any(value.get(key) != item for key, item in expected.items())
    ):
        raise FinalReviewSubmissionError("final-review intent conflict")
    baseline = _pre_submission_contents(contents)
    expected_digests = _digests(baseline)
    expected_hashes = _hashes(baseline)
    if _status_value(state) == _Status.FINAL_REVIEW_SUBMITTED.value:
        expected_digests["state"] = value.get("pre_run_state_sha256")
    if (
        value.get("pre_digests") != expected_digests
        or value.get("pre_hashes") != expected_hashes
        or value.get("part_one_request_sha256")
        != _sha(baseline["final-review-part-0001-requests.jsonl"])
    ):
        raise FinalReviewSubmissionError("final-review evidence changed")
    if _FINAL_INTENT_NAME in contents or _FINAL_COMPLETED_NAME in contents:
        expected_journals = set(value.get("pre_hashes", {}).get("journals", {})) | {
            _FINAL_INTENT_NAME,
            _FINAL_COMPLETED_NAME,
        }
        if set(_classes(contents)[1]) != expected_journals:
            raise FinalReviewSubmissionError("final-review application journal invalid")
    return dict(value)


def _receipt_payload(
    intent: Mapping[str, object],
    *,
    intent_sha: str,
    result: Mapping[str, object],
    contents: Mapping[str, bytes],
) -> dict[str, object]:
    return {
        **dict(intent),
        "intent_sha256": intent_sha,
        "status": "receipt",
        "result": dict(result),
        "post_digests": _digests(contents),
        "post_hashes": _hashes(contents),
        "post_run_state_sha256": _sha(contents[STATE_NAME]),
    }


def _validate_receipt(
    value: object,
    *,
    intent: Mapping[str, object],
    intent_sha: str,
    result: Mapping[str, object],
    contents: Mapping[str, bytes],
) -> None:
    if value != _receipt_payload(intent, intent_sha=intent_sha, result=result, contents=contents):
        raise FinalReviewSubmissionError("final-review receipt invalid")


def _validate_transition(
    before: Mapping[str, bytes],
    after: Mapping[str, bytes],
    before_state: object,
    after_state: object,
) -> None:
    if any(
        name not in after or after[name] != raw
        for name, raw in before.items()
        if name != STATE_NAME
    ):
        raise FinalReviewSubmissionError("final-review immutable evidence changed")
    before_journals = {
        name for name in (_FINAL_INTENT_NAME, _FINAL_COMPLETED_NAME) if name in before
    }
    expected_new = (
        {_FINAL_INTENT_NAME, _FINAL_COMPLETED_NAME} if not before_journals else set()
    )
    if (
        before_journals not in (set(), {_FINAL_INTENT_NAME, _FINAL_COMPLETED_NAME})
        or set(after) - set(before) != expected_new
        or _status_value(after_state) != _Status.FINAL_REVIEW_SUBMITTED.value
        or getattr(after_state, "final_review_completed_part_count") != 0
        or len(getattr(after_state, "final_review_batch_ids")) != 1
        or len(getattr(after_state, "final_review_input_file_ids")) != 1
    ):
        raise FinalReviewSubmissionError("final-review post-inventory invalid")
    old, new = before_state.to_dict(), after_state.to_dict()
    if set(old) != set(new) or any(
        old[key] != new[key] for key in old if key not in ROOT_STATE_MUTABLE
    ):
        raise FinalReviewSubmissionError("final-review immutable state changed")


def _validate_reconciliation_transition(
    before: Mapping[str, bytes],
    after: Mapping[str, bytes],
    before_state: object,
    after_state: object,
    request: Mapping[str, object],
) -> None:
    if (
        before_state.to_dict() != after_state.to_dict()
        or any(name not in after or after[name] != raw for name, raw in before.items())
        or set(after) - set(before) != {_FINAL_INTENT_NAME}
        or _validate_application_journals(
            after,
            request,
            after_state,
            _sha(after["final-review-part-0001-requests.jsonl"]),
        )
        != (True, False)
    ):
        raise FinalReviewSubmissionError("final-review reconciliation evidence invalid")


def process_request(request: Mapping[str, object]) -> dict[str, object]:
    try:
        request = contract.validate_request(request)
    except (TypeError, ValueError) as error:
        raise FinalReviewSubmissionError("invalid final-review request") from error
    auth_sha = _validate_authorization(request)
    run = _run_directory(request)
    contents, state = _inventory(run)
    predecessor = _validate_phase78_predecessor(request, run, state, contents)
    estimated = _estimate_final_review_cost(contents, state)
    _validate_budget(request, state, estimated)
    app_intent, app_completed = _validate_application_journals(
        contents, request, state, _sha(contents["final-review-part-0001-requests.jsonl"])
    )
    claim_path, intent_path, receipt_path = (
        RECEIPTS_ROOT / f"authorization-{request['authorization_id']}.claim.json",
        RECEIPTS_ROOT / f"{request['run_id']}.intent.json",
        RECEIPTS_ROOT / f"{request['run_id']}.json",
    )
    _directory(RECEIPTS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    exists = {path: os.path.lexists(path) for path in (claim_path, intent_path, receipt_path)}
    for path in (claim_path, intent_path, receipt_path):
        if os.path.lexists(path.with_name(f".{path.name}.pending")) and not exists[path]:
            raise FinalReviewSubmissionError("final-review receipt state ambiguous")
    if exists[receipt_path] and not (exists[claim_path] and exists[intent_path]):
        raise FinalReviewSubmissionError("orphan final-review receipt")
    if exists[intent_path] != exists[claim_path]:
        raise FinalReviewSubmissionError("orphan final-review intent")
    claim = _claim_payload(request, auth_sha)
    if exists[claim_path]:
        stored_claim, claim_sha = _record(claim_path)
        if stored_claim != claim:
            raise FinalReviewSubmissionError("final-review authorization conflict")
    else:
        claim_sha = _write_once(claim_path, claim)
    if _status_value(state) == _Status.FINAL_REVIEW_SUBMITTED.value:
        if not exists[intent_path]:
            raise FinalReviewSubmissionError("missing final-review intent")
        stored_intent, intent_sha = _record(intent_path)
        expected = _intent_payload(
            request,
            auth_sha=auth_sha,
            claim_sha=claim_sha,
            predecessor=predecessor,
            contents=contents,
            state=state,
            estimated=estimated,
        )
        for key in (
            "pre_digests",
            "pre_hashes",
            "pre_run_state_sha256",
            "pre_artifact_set_sha256",
            "pre_journal_set_sha256",
            "pre_output_set_sha256",
            "pre_derived_set_sha256",
        ):
            expected[key] = stored_intent.get(key)
        intent = _validate_intent(stored_intent, expected, contents=contents, state=state)
        result = _aggregate(request, state, status="submitted", estimated=estimated, submitted=1)
        if not exists[receipt_path]:
            _write_once(
                receipt_path,
                _receipt_payload(intent, intent_sha=intent_sha, result=result, contents=contents),
            )
            return result
        stored_receipt, _ = _record(receipt_path)
        _validate_receipt(
            stored_receipt, intent=intent, intent_sha=intent_sha, result=result, contents=contents
        )
        return _aggregate(
            request, state, status="already_submitted", estimated=estimated, submitted=1
        )
    if _status_value(state) != _Status.FINAL_REVIEW_PREPARED.value:
        raise FinalReviewSubmissionError("final-review checkpoint invalid")
    if exists[intent_path]:
        stored_intent, intent_sha = _record(intent_path)
        expected = _intent_payload(
            request,
            auth_sha=auth_sha,
            claim_sha=claim_sha,
            predecessor=predecessor,
            contents=contents,
            state=state,
            estimated=estimated,
        )
        for key in (
            "pre_digests",
            "pre_hashes",
            "pre_run_state_sha256",
            "pre_artifact_set_sha256",
            "pre_journal_set_sha256",
            "pre_output_set_sha256",
            "pre_derived_set_sha256",
        ):
            expected[key] = stored_intent.get(key)
        intent = _validate_intent(stored_intent, expected, contents=contents, state=state)
        if not app_intent or not app_completed:
            return _aggregate(
                request, state, status="reconciliation_required", estimated=estimated, submitted=0
            )
    else:
        if app_intent or app_completed:
            return _aggregate(
                request, state, status="reconciliation_required", estimated=estimated, submitted=0
            )
        intent = _intent_payload(
            request,
            auth_sha=auth_sha,
            claim_sha=claim_sha,
            predecessor=predecessor,
            contents=contents,
            state=state,
            estimated=estimated,
        )
        intent_sha = _write_once(intent_path, intent)
    binding_contents = (
        _pre_submission_contents(contents)
        if _status_value(state) == _Status.FINAL_REVIEW_PREPARED.value
        else contents
    )
    digests = _digests(binding_contents)
    bindings = {
        contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: digests["state"],
        contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: digests["artifacts"],
        contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: digests["journals"],
        contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256: digests["outputs"],
        contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256: digests["derived"],
        contract.ENV_EXPECTED_REQUEST_SHA256: _sha(
            binding_contents["final-review-part-0001-requests.jsonl"]
        ),
    }
    result = _run_worker(request, run, bindings)
    if result.get("status") == "reconciliation_required":
        after_contents, after_state = _inventory(run)
        _validate_reconciliation_transition(
            contents,
            after_contents,
            state,
            after_state,
            request,
        )
        return _aggregate(
            request, state, status="reconciliation_required", estimated=estimated, submitted=0
        )
    try:
        contract.validate_aggregate(result, status="submitted")
    except (TypeError, ValueError) as error:
        raise FinalReviewSubmissionError("final-review worker result invalid") from error
    if (
        result.get("run_id") != request["run_id"]
        or result.get("run_status") != "final_review_submitted"
    ):
        raise FinalReviewSubmissionError("final-review worker result invalid")
    after_contents, after_state = _inventory(run)
    _validate_transition(contents, after_contents, state, after_state)
    final = _aggregate(request, after_state, status="submitted", estimated=estimated, submitted=1)
    if result != final:
        raise FinalReviewSubmissionError("final-review worker result invalid")
    _write_once(
        receipt_path,
        _receipt_payload(intent, intent_sha=intent_sha, result=final, contents=after_contents),
    )
    return final


worker = None
if not sys.flags.no_site:
    try:
        worker = importlib.import_module("scripts.submit_final_private_speaker_review_workspace")
    except Exception:
        worker = None


def _require_root() -> None:
    if (
        os.name != "posix"
        or not hasattr(os, "geteuid")
        or os.geteuid() != ROOT_UID
        or os.environ.get("SUDO_USER") != host.REVIEW_USER
        or platform.system() != "Linux"
        or platform.machine() != "x86_64"
    ):
        raise FinalReviewSubmissionError("invalid final-review caller")


def main() -> int:
    try:
        _require_root()
        sys.stdout.buffer.write(
            contract.canonical_json(process_request(_read_request(sys.stdin.buffer)))
        )
        return 0
    except Exception:
        sys.stderr.write("error=speaker_review_final_review_rejected\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
