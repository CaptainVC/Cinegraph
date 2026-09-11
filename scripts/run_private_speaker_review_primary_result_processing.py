"""Root-only coordinator for provider-free primary-result processing.

The coordinator binds a fresh authorization to the complete two-part primary
receipt chain, an immutable source workspace, and five disjoint run-inventory
digests.  It launches only the offline Phase 67 worker and publishes a
root-owned receipt after independently validating the filesystem transition.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import platform
import re
import signal
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import IO, BinaryIO, Final, Mapping

_SCRIPTS = Path(__file__).resolve().parent
_RELEASE_ROOT = _SCRIPTS.parent
for _root in (_RELEASE_ROOT, _RELEASE_ROOT / "src"):
    if os.fspath(_root) not in sys.path:
        sys.path.insert(0, os.fspath(_root))

try:
    from scripts import private_speaker_review_primary_result_processing_contract as contract
    from scripts import (
        private_speaker_review_primary_result_processing_host_contract as host,
    )
    from scripts import run_private_speaker_review as preparation
    from scripts import run_private_speaker_review_next_primary as next_submission
    from scripts import (
        run_private_speaker_review_next_primary_observation as final_observation,
    )
    from scripts import run_private_speaker_review_observation as observation
except ModuleNotFoundError:
    import private_speaker_review_primary_result_processing_contract as contract
    import private_speaker_review_primary_result_processing_host_contract as host
    import run_private_speaker_review as preparation
    import run_private_speaker_review_next_primary as next_submission
    import run_private_speaker_review_next_primary_observation as final_observation
    import run_private_speaker_review_observation as observation


class PrimaryResultProcessingError(RuntimeError):
    """Generic rejection that never exposes private or provider details."""


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    state: bytes
    artifacts: dict[str, bytes]
    journals: dict[str, bytes]
    outputs: dict[str, bytes]
    derived: dict[str, bytes]


RELEASE_ROOT: Final = _RELEASE_ROOT
RUNS_ROOT: Final = host.SPEAKER_REVIEW_RUNS_ROOT
SOURCE_ROOT: Final = host.SPEAKER_REVIEW_SOURCE_ROOT
AUTHORIZATION_ROOT: Final = host.REVIEW_AUTHORIZATION_ROOT
RECEIPTS_ROOT: Final = host.REVIEW_PRIMARY_RESULT_PROCESSING_RECEIPTS_ROOT
COMPOSE_PATH: Final = RELEASE_ROOT / "deploy/compose.yaml"
ENV_FILE: Final = host.ENV_FILE
SOURCE_TARGET: Final = "/review-workspace"
RUNS_TARGET: Final = "/review-workspace/review-runs"
ROOT_UID: Final = 0
ROOT_GID: Final = 0
WORKER_UID: Final = host.UID_IN_CONTAINER
WORKER_GID: Final = host.GID_IN_CONTAINER
MAX_RECORD_BYTES: Final = 128 * 1024
MAX_STATE_BYTES: Final = 64 * 1024
MAX_FILE_BYTES: Final = 64 * 1024 * 1024
MAX_TOTAL_BYTES: Final = 256 * 1024 * 1024
WORKER_SHUTDOWN_SECONDS: Final = 60
WORKER_TIMEOUT_SECONDS: Final = (
    host.REVIEW_PRIMARY_RESULT_PROCESSING_TIMEOUT_SECONDS - WORKER_SHUTDOWN_SECONDS
)
WORKER_TERMINATION_SECONDS: Final = min(
    WORKER_SHUTDOWN_SECONDS,
    host.REVIEW_PRIMARY_RESULT_PROCESSING_KILL_AFTER_SECONDS,
)
STATE: Final = "run-state.json"
PRIMARY_DERIVED: Final = frozenset(
    {"primary-verdicts.jsonl", "primary-parse-errors.json", "primary-decisions.jsonl"}
)
_EPISODE_NAME: Final = re.compile(r"\b(?P<season>[0-9]+)x(?P<episode>[0-9]{2})\b")
_MUTABLE_STATE_FIELDS: Final = frozenset(
    {
        "status",
        "updated_at",
        "actual_primary_cost_usd",
        "actual_total_cost_usd",
        "accepted_by_consensus",
        "adjudication_part_count",
        "needs_human",
    }
)
_BINDING_KEYS: Final = frozenset(
    {
        "archive_sha256",
        "authorization_id",
        "authorization_sha256",
        "candidate_count",
        "configuration_sha256",
        "final_observation_intent_sha256",
        "final_observation_receipt_sha256",
        "image_reference",
        "maximum_authorized_cost_microusd",
        "operation",
        "pre_artifact_hashes",
        "pre_artifact_set_sha256",
        "pre_derived_hashes",
        "pre_derived_set_sha256",
        "pre_journal_hashes",
        "pre_journal_set_sha256",
        "pre_output_hashes",
        "pre_output_set_sha256",
        "pre_run_state_sha256",
        "pre_state_binding_sha256",
        "prep_receipt_sha256",
        "primary_part_count",
        "purpose",
        "release_sha",
        "run_id",
        "schema_version",
        "season_number",
        "source_manifest_sha256",
        "status",
    }
)
_RECEIPT_KEYS: Final = frozenset(
    {
        *_BINDING_KEYS,
        "post_artifact_file_count",
        "post_artifact_set_sha256",
        "post_derived_file_count",
        "post_derived_set_sha256",
        "post_journal_file_count",
        "post_journal_set_sha256",
        "post_output_file_count",
        "post_output_set_sha256",
        "post_run_state_sha256",
        "result",
    }
)


def _canonical(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _set_digest(contents: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(contents):
        encoded = name.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(contents[name]).to_bytes(8, "big"))
        digest.update(contents[name])
    return digest.hexdigest()


def _hashes(contents: Mapping[str, bytes]) -> dict[str, str]:
    return {name: _sha(raw) for name, raw in sorted(contents.items())}


def _is_sha(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


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
        metadata = path.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or path.resolve(strict=True) != path
            or (os.name == "posix" and stat.S_IMODE(metadata.st_mode) != mode)
            or (os.name == "posix" and (metadata.st_uid, metadata.st_gid) != owner)
        ):
            raise OSError
    except OSError as error:
        raise PrimaryResultProcessingError("processing evidence unavailable") from error


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
        raise PrimaryResultProcessingError("processing evidence unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _decode(raw: bytes) -> dict[str, object]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=next_submission._unique_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise PrimaryResultProcessingError("processing evidence invalid") from error
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise PrimaryResultProcessingError("processing evidence invalid")
    return value


def _read_request(stream: BinaryIO) -> dict[str, object]:
    raw = stream.readline(contract.REQUEST_MAX_BYTES + 1)
    if stream.read(1):
        raise PrimaryResultProcessingError("invalid processing request")
    try:
        return contract.parse_request(raw)
    except (TypeError, ValueError) as error:
        raise PrimaryResultProcessingError("invalid processing request") from error


def _read_record(path: Path) -> tuple[dict[str, object], str]:
    _repair_linked_publication(path)
    raw = _stable(
        path,
        maximum=MAX_RECORD_BYTES,
        mode=0o600,
        owner=(ROOT_UID, ROOT_GID),
    )
    return _decode(raw), _sha(raw)


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
        raise PrimaryResultProcessingError("processing authorization invalid") from error
    return _sha(raw)


def _run_directory(request: Mapping[str, object]) -> tuple[Path, Path]:
    _directory(RUNS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    object_root = RUNS_ROOT / f"sha256-{request['archive_sha256']}"
    runs = object_root / "review-runs"
    _directory(object_root, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    _directory(runs, mode=0o700, owner=(WORKER_UID, WORKER_GID))
    run = runs / str(request["run_id"])
    try:
        if run.resolve(strict=False).parent != runs.resolve(strict=True):
            raise OSError
    except OSError as error:
        raise PrimaryResultProcessingError("processing run invalid") from error
    _directory(run, mode=0o700, owner=(WORKER_UID, WORKER_GID))
    return runs, run


def _source_workspace(request: Mapping[str, object]) -> Path:
    _directory(SOURCE_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    try:
        object_root, manifest, files, install_receipt = preparation._verified_object(
            str(request["archive_sha256"])
        )
        source = SOURCE_ROOT / f"sha256-{request['archive_sha256']}"
        preparation._verify_source_workspace(source, manifest, files, install_receipt)
        # Revalidation of the installed object and copied immutable view is
        # intentionally coupled: neither digest alone proves both still exist.
        if not object_root.is_dir():
            raise OSError
        return source
    except Exception as error:
        raise PrimaryResultProcessingError("processing source unavailable") from error


def _read_inventory(run: Path) -> RunSnapshot:
    _directory(run, mode=0o700, owner=(WORKER_UID, WORKER_GID))
    files: dict[str, bytes] = {}
    directories: dict[str, tuple[int, int, int, int, int, int, int, int]] = {}
    total = 0
    try:
        for current, children, names in os.walk(run, followlinks=False):
            current_path = Path(current)
            relative_directory = current_path.relative_to(run).as_posix()
            metadata = current_path.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or (os.name == "posix" and stat.S_IMODE(metadata.st_mode) != 0o700)
                or (
                    os.name == "posix"
                    and (metadata.st_uid, metadata.st_gid) != (WORKER_UID, WORKER_GID)
                )
            ):
                raise OSError
            directories[relative_directory] = _identity(metadata)
            for child_name in children:
                child = current_path / child_name
                child_metadata = child.lstat()
                if not stat.S_ISDIR(child_metadata.st_mode) or stat.S_ISLNK(child_metadata.st_mode):
                    raise OSError
            for name in names:
                path = current_path / name
                relative = path.relative_to(run).as_posix()
                raw = _stable(
                    path,
                    maximum=MAX_STATE_BYTES if relative == STATE else MAX_FILE_BYTES,
                    mode=0o600,
                    owner=(WORKER_UID, WORKER_GID),
                )
                total += len(raw)
                if total > MAX_TOTAL_BYTES:
                    raise OSError
                files[relative] = raw
        for name, identity in directories.items():
            path = run if name == "." else run / name
            if _identity(path.lstat()) != identity:
                raise OSError
    except (OSError, RuntimeError, ValueError) as error:
        raise PrimaryResultProcessingError("processing inventory invalid") from error
    if STATE not in files:
        raise PrimaryResultProcessingError("processing inventory invalid")
    artifacts: dict[str, bytes] = {}
    journals: dict[str, bytes] = {}
    outputs: dict[str, bytes] = {}
    derived: dict[str, bytes] = {}
    for name, raw in files.items():
        if name == STATE:
            continue
        if name.startswith(".primary-part-"):
            journals[name] = raw
        elif name.startswith("primary-part-") and (
            name.endswith("-output.jsonl") or name.endswith("-api-errors.jsonl")
        ):
            outputs[name] = raw
        elif name in {"candidates.jsonl", "source-manifest.json"} or (
            name.startswith("primary-part-") and name.endswith("-requests.jsonl")
        ):
            artifacts[name] = raw
        else:
            derived[name] = raw
    derived.update({f"{name}/": b"" for name in directories if name != "."})
    return RunSnapshot(files[STATE], artifacts, journals, outputs, derived)


def _state(snapshot: RunSnapshot) -> dict[str, object]:
    value = _decode(snapshot.state)
    if set(value) != observation._RUN_STATE_KEYS:
        raise PrimaryResultProcessingError("processing run state invalid")
    return value


def _expected_inventory(
    state: Mapping[str, object],
) -> tuple[set[str], set[str], set[str], set[str]]:
    part_count = state.get("primary_part_count")
    if type(part_count) is not int or part_count <= 0:
        raise PrimaryResultProcessingError("processing run state invalid")
    artifacts = {
        "candidates.jsonl",
        "source-manifest.json",
        *(f"primary-part-{part:04d}-requests.jsonl" for part in range(1, part_count + 1)),
    }
    journals = {
        f".primary-part-{part:04d}-{kind}.json"
        for part in range(1, part_count + 1)
        for kind in ("submission-intent", "submission-completed")
    }
    required_outputs = {
        f"primary-part-{part:04d}-output.jsonl" for part in range(1, part_count + 1)
    }
    optional_outputs = {
        f"primary-part-{part:04d}-api-errors.jsonl" for part in range(1, part_count + 1)
    }
    return artifacts, journals, required_outputs, optional_outputs


def _completed_derived(source_manifest: bytes) -> set[str]:
    try:
        value = json.loads(
            source_manifest.decode("utf-8"),
            object_pairs_hook=next_submission._unique_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        sources = value.get("sources")
        if not isinstance(value, dict) or not isinstance(sources, dict) or not sources:
            raise ValueError
        expected = {
            *PRIMARY_DERIVED,
            "review-ledger.json",
            "calibration-sample.json",
            "reviewed/",
        }
        for name in sources:
            if (
                not isinstance(name, str)
                or Path(name).name != name
                or not name.endswith(".script-aligned.srt")
            ):
                raise ValueError
            episode = _EPISODE_NAME.search(Path(name).name)
            if episode is None:
                raise ValueError
            directory = f"reviewed/season-{int(episode.group('season')):02d}"
            reviewed = name.removesuffix(".script-aligned.srt") + ".automated-reviewed.srt"
            expected.add(f"{directory}/")
            expected.add(f"{directory}/{reviewed}")
        return expected
    except (AttributeError, KeyError, TypeError, UnicodeError, ValueError) as error:
        raise PrimaryResultProcessingError("processing source manifest invalid") from error


def _allowed_partial_derived(state: Mapping[str, object]) -> set[str]:
    candidates = state.get("candidate_count")
    if type(candidates) is not int or candidates <= 0:
        raise PrimaryResultProcessingError("processing run state invalid")
    return {
        *PRIMARY_DERIVED,
        *(f"adjudication-part-{part:04d}-requests.jsonl" for part in range(1, candidates + 1)),
    }


def _validate_inventory_shape(snapshot: RunSnapshot, state: Mapping[str, object]) -> None:
    artifacts, journals, required_outputs, optional_outputs = _expected_inventory(state)
    if (
        set(snapshot.artifacts) != artifacts
        or set(snapshot.journals) != journals
        or not required_outputs <= set(snapshot.outputs) <= required_outputs | optional_outputs
    ):
        raise PrimaryResultProcessingError("processing inventory invalid")
    status = state.get("status")
    if status == "primary_part_completed":
        if not set(snapshot.derived) <= _allowed_partial_derived(state):
            raise PrimaryResultProcessingError("processing inventory invalid")
    elif status == "adjudication_prepared":
        part_count = state.get("adjudication_part_count")
        if type(part_count) is not int or part_count <= 0:
            raise PrimaryResultProcessingError("processing inventory invalid")
        expected = {
            *PRIMARY_DERIVED,
            *(f"adjudication-part-{part:04d}-requests.jsonl" for part in range(1, part_count + 1)),
        }
        if set(snapshot.derived) != expected:
            raise PrimaryResultProcessingError("processing inventory invalid")
    elif status == "completed":
        if set(snapshot.derived) != _completed_derived(snapshot.artifacts["source-manifest.json"]):
            raise PrimaryResultProcessingError("processing inventory invalid")
    else:
        raise PrimaryResultProcessingError("processing checkpoint invalid")


def _state_binding(state: Mapping[str, object]) -> str:
    return _sha(
        _canonical({key: value for key, value in state.items() if key not in _MUTABLE_STATE_FIELDS})
    )


def _validate_pre_state(state: Mapping[str, object], prep: Mapping[str, object]) -> None:
    try:
        next_submission._validate_state_shape(state, prep)
        if (
            state.get("status") != "primary_part_completed"
            or state.get("primary_completed_part_count") != state.get("primary_part_count")
            or state.get("candidate_count") != prep["result"]["candidate_count"]
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, next_submission.NextPrimaryProcessingError) as error:
        raise PrimaryResultProcessingError("processing checkpoint invalid") from error


def _phase66_evidence(
    request: Mapping[str, object],
    prep: Mapping[str, object],
    snapshot: RunSnapshot,
    state: Mapping[str, object],
) -> tuple[str, str]:
    intent_path = observation._root_intent_path(str(request["run_id"]), 2)
    receipt_path = observation._root_receipt_path(str(request["run_id"]), 2)
    intent, intent_sha = _read_record(intent_path)
    receipt, receipt_sha = _read_record(receipt_path)
    authorization_id = intent.get("authorization_id")
    if not isinstance(authorization_id, str):
        raise PrimaryResultProcessingError("final observation evidence invalid")
    prior_request = {
        "archive_sha256": request["archive_sha256"],
        "authorization_id": authorization_id,
        "maximum_authorized_cost_microusd": request["maximum_authorized_cost_microusd"],
        "operation": final_observation.contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": request["run_id"],
        "schema_version": final_observation.contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
    }
    raw = _stable(
        AUTHORIZATION_ROOT / f"{authorization_id}.json",
        maximum=final_observation.contract.REQUEST_MAX_BYTES,
        mode=0o600,
        owner=(ROOT_UID, ROOT_GID),
    )
    try:
        if final_observation.contract.parse_request(raw) != prior_request:
            raise ValueError
        validated = final_observation._validate_intent(
            intent,
            request=prior_request,
            prep=prep,
            authorization_sha256=_sha(raw),
        )
        final_observation._validate_predecessors_from_intent(validated, request=prior_request)
        evidence = {
            STATE: snapshot.state,
            **snapshot.artifacts,
            **snapshot.journals,
            **snapshot.outputs,
        }
        final_observation._validate_recovered_transition(
            evidence, state, validated, status="observed"
        )
        receipt_result = final_observation._validate_final_receipt(
            receipt,
            intent=validated,
            contents=evidence,
            expected_status="observed",
        )
        if receipt_result != final_observation._aggregate(prior_request, prep, state, "observed"):
            raise ValueError
    except Exception as error:
        raise PrimaryResultProcessingError("final observation evidence invalid") from error
    return intent_sha, receipt_sha


def _intent_payload(
    request: Mapping[str, object],
    prep: Mapping[str, object],
    authorization_sha: str,
    observation_intent_sha: str,
    observation_receipt_sha: str,
    snapshot: RunSnapshot,
    state: Mapping[str, object],
) -> dict[str, object]:
    value: dict[str, object] = {
        "archive_sha256": request["archive_sha256"],
        "authorization_id": request["authorization_id"],
        "authorization_sha256": authorization_sha,
        "candidate_count": state["candidate_count"],
        "configuration_sha256": prep["config_sha"],
        "final_observation_intent_sha256": observation_intent_sha,
        "final_observation_receipt_sha256": observation_receipt_sha,
        "image_reference": prep["image"],
        "maximum_authorized_cost_microusd": request["maximum_authorized_cost_microusd"],
        "operation": contract.OPERATION,
        "pre_artifact_hashes": _hashes(snapshot.artifacts),
        "pre_artifact_set_sha256": _set_digest(snapshot.artifacts),
        "pre_derived_hashes": _hashes(snapshot.derived),
        "pre_derived_set_sha256": _set_digest(snapshot.derived),
        "pre_journal_hashes": _hashes(snapshot.journals),
        "pre_journal_set_sha256": _set_digest(snapshot.journals),
        "pre_output_hashes": _hashes(snapshot.outputs),
        "pre_output_set_sha256": _set_digest(snapshot.outputs),
        "pre_run_state_sha256": _sha(snapshot.state),
        "pre_state_binding_sha256": _state_binding(state),
        "prep_receipt_sha256": prep["receipt_sha"],
        "primary_part_count": state["primary_part_count"],
        "purpose": contract.PURPOSE,
        "release_sha": prep["release_sha"],
        "run_id": request["run_id"],
        "schema_version": contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
        "source_manifest_sha256": _sha(snapshot.artifacts["source-manifest.json"]),
        "status": "intent",
    }
    if set(value) != _BINDING_KEYS:
        raise PrimaryResultProcessingError("processing intent invalid")
    return value


def _validate_hash_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or any(
        not isinstance(name, str) or not _is_sha(digest) for name, digest in value.items()
    ):
        raise PrimaryResultProcessingError("processing intent invalid")
    return dict(value)


def _validate_intent(
    value: object,
    *,
    request: Mapping[str, object],
    prep: Mapping[str, object],
    authorization_sha: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _BINDING_KEYS:
        raise PrimaryResultProcessingError("processing intent invalid")
    for field in (
        "pre_artifact_hashes",
        "pre_derived_hashes",
        "pre_journal_hashes",
        "pre_output_hashes",
    ):
        _validate_hash_map(value.get(field))
    artifact_hashes = _validate_hash_map(value.get("pre_artifact_hashes"))
    derived_hashes = _validate_hash_map(value.get("pre_derived_hashes"))
    if (
        value.get("schema_version") != contract.PROTOCOL_VERSION
        or value.get("operation") != contract.OPERATION
        or value.get("purpose") != contract.PURPOSE
        or value.get("season_number") != contract.SEASON_NUMBER
        or value.get("status") != "intent"
        or value.get("archive_sha256") != request["archive_sha256"]
        or value.get("run_id") != request["run_id"]
        or value.get("authorization_id") != request["authorization_id"]
        or value.get("authorization_sha256") != authorization_sha
        or value.get("maximum_authorized_cost_microusd")
        != request["maximum_authorized_cost_microusd"]
        or value.get("prep_receipt_sha256") != prep["receipt_sha"]
        or value.get("release_sha") != prep["release_sha"]
        or value.get("image_reference") != prep["image"]
        or value.get("configuration_sha256") != prep["config_sha"]
        or value.get("primary_part_count") != prep["result"]["primary_part_count"]
        or value.get("candidate_count") != prep["result"]["candidate_count"]
        or not _is_sha(value.get("final_observation_intent_sha256"))
        or not _is_sha(value.get("final_observation_receipt_sha256"))
        or not _is_sha(value.get("source_manifest_sha256"))
        or artifact_hashes.get("source-manifest.json") != value.get("source_manifest_sha256")
        or derived_hashes
        or value.get("pre_derived_set_sha256") != _set_digest({})
        or any(
            not _is_sha(value.get(field))
            for field in (
                "pre_artifact_set_sha256",
                "pre_derived_set_sha256",
                "pre_journal_set_sha256",
                "pre_output_set_sha256",
                "pre_run_state_sha256",
                "pre_state_binding_sha256",
            )
        )
    ):
        raise PrimaryResultProcessingError("processing intent invalid")
    return dict(value)


def _validate_predecessor_replay(
    intent: Mapping[str, object],
    request: Mapping[str, object],
    prep: Mapping[str, object],
    state: Mapping[str, object],
) -> None:
    prior_intent, prior_intent_sha = _read_record(
        observation._root_intent_path(str(request["run_id"]), 2)
    )
    prior_receipt, prior_receipt_sha = _read_record(
        observation._root_receipt_path(str(request["run_id"]), 2)
    )
    authorization_id = prior_intent.get("authorization_id")
    if not isinstance(authorization_id, str):
        raise PrimaryResultProcessingError("final observation evidence invalid")
    prior_request = {
        "archive_sha256": request["archive_sha256"],
        "authorization_id": authorization_id,
        "maximum_authorized_cost_microusd": request["maximum_authorized_cost_microusd"],
        "operation": final_observation.contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": request["run_id"],
        "schema_version": final_observation.contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
    }
    raw = _stable(
        AUTHORIZATION_ROOT / f"{authorization_id}.json",
        maximum=final_observation.contract.REQUEST_MAX_BYTES,
        mode=0o600,
        owner=(ROOT_UID, ROOT_GID),
    )
    try:
        authorization = final_observation.contract.parse_request(raw)
        result = final_observation.contract.validate_aggregate(
            prior_receipt.get("result"), status="observed"
        )
        expected_result = final_observation._aggregate(prior_request, prep, state, "observed")
        validated = final_observation._validate_intent(
            prior_intent,
            request=prior_request,
            prep=prep,
            authorization_sha256=_sha(raw),
        )
        final_observation._validate_predecessors_from_intent(validated, request=prior_request)
        if (
            authorization != prior_request
            or prior_intent_sha != intent["final_observation_intent_sha256"]
            or prior_receipt_sha != intent["final_observation_receipt_sha256"]
            or set(prior_receipt) != final_observation._RECEIPT_KEYS
            or prior_receipt.get("status") != "observed"
            or any(
                prior_receipt.get(key) != item
                for key, item in prior_intent.items()
                if key != "status"
            )
            or prior_receipt.get("post_run_state_sha256") != intent["pre_run_state_sha256"]
            or prior_receipt.get("post_journal_file_count")
            != len(_validate_hash_map(intent["pre_journal_hashes"]))
            or prior_receipt.get("post_journal_set_sha256") != intent["pre_journal_set_sha256"]
            or prior_receipt.get("post_artifact_file_count")
            != (
                1
                + len(_validate_hash_map(intent["pre_artifact_hashes"]))
                + len(_validate_hash_map(intent["pre_journal_hashes"]))
                + len(_validate_hash_map(intent["pre_output_hashes"]))
            )
            or not _is_sha(prior_receipt.get("post_artifact_set_sha256"))
            or result != expected_result
        ):
            raise ValueError
    except Exception as error:
        raise PrimaryResultProcessingError("final observation evidence changed") from error


def _original_groups_unchanged(snapshot: RunSnapshot, intent: Mapping[str, object]) -> None:
    groups = (
        (snapshot.artifacts, "pre_artifact_hashes", "pre_artifact_set_sha256"),
        (snapshot.journals, "pre_journal_hashes", "pre_journal_set_sha256"),
        (snapshot.outputs, "pre_output_hashes", "pre_output_set_sha256"),
    )
    for contents, hashes_field, digest_field in groups:
        expected = _validate_hash_map(intent[hashes_field])
        if _hashes(contents) != expected or _set_digest(contents) != intent[digest_field]:
            raise PrimaryResultProcessingError("processing immutable evidence changed")
    if _sha(snapshot.artifacts["source-manifest.json"]) != intent["source_manifest_sha256"]:
        raise PrimaryResultProcessingError("processing immutable evidence changed")


def _validate_recovery(
    snapshot: RunSnapshot, state: Mapping[str, object], intent: Mapping[str, object]
) -> None:
    _original_groups_unchanged(snapshot, intent)
    original_derived = _validate_hash_map(intent["pre_derived_hashes"])
    if (
        not set(original_derived) <= set(snapshot.derived)
        or any(_sha(snapshot.derived[name]) != digest for name, digest in original_derived.items())
        or _state_binding(state) != intent["pre_state_binding_sha256"]
    ):
        raise PrimaryResultProcessingError("processing recovery evidence changed")
    status = state.get("status")
    if status == "primary_part_completed":
        if _sha(snapshot.state) != intent["pre_run_state_sha256"] or not set(
            snapshot.derived
        ) <= _allowed_partial_derived(state):
            raise PrimaryResultProcessingError("processing recovery evidence changed")
    elif status not in {"adjudication_prepared", "completed"}:
        raise PrimaryResultProcessingError("processing recovery checkpoint invalid")


def _aggregate(
    request: Mapping[str, object], state: Mapping[str, object], status: str
) -> dict[str, object]:
    value = {
        "accepted_by_consensus": state["accepted_by_consensus"],
        "adjudication_part_count": state["adjudication_part_count"],
        "candidate_count": state["candidate_count"],
        "operation": contract.OPERATION,
        "primary_completed_part_count": state["primary_completed_part_count"],
        "primary_part_count": state["primary_part_count"],
        "purpose": contract.PURPOSE,
        "run_id": request["run_id"],
        "run_status": state["status"],
        "season_number": contract.SEASON_NUMBER,
        "status": status,
        "needs_human": state["needs_human"],
    }
    try:
        return contract.validate_aggregate(value, status=status)
    except (KeyError, TypeError, ValueError) as error:
        raise PrimaryResultProcessingError("processing aggregate invalid") from error


def _receipt_payload(
    intent: Mapping[str, object], result: Mapping[str, object], snapshot: RunSnapshot
) -> dict[str, object]:
    value = {
        **intent,
        "post_artifact_file_count": len(snapshot.artifacts),
        "post_artifact_set_sha256": _set_digest(snapshot.artifacts),
        "post_derived_file_count": len(snapshot.derived),
        "post_derived_set_sha256": _set_digest(snapshot.derived),
        "post_journal_file_count": len(snapshot.journals),
        "post_journal_set_sha256": _set_digest(snapshot.journals),
        "post_output_file_count": len(snapshot.outputs),
        "post_output_set_sha256": _set_digest(snapshot.outputs),
        "post_run_state_sha256": _sha(snapshot.state),
        "result": dict(result),
        "status": result["status"],
    }
    if set(value) != _RECEIPT_KEYS:
        raise PrimaryResultProcessingError("processing receipt invalid")
    return value


def _validate_receipt(
    value: object,
    *,
    intent: Mapping[str, object],
    snapshot: RunSnapshot,
    expected_status: str,
    expected_result: Mapping[str, object],
) -> dict[str, object]:
    if (
        not isinstance(value, dict)
        or set(value) != _RECEIPT_KEYS
        or any(value.get(key) != item for key, item in intent.items() if key != "status")
        or value.get("status") != expected_status
        or value.get("post_artifact_file_count") != len(snapshot.artifacts)
        or value.get("post_artifact_set_sha256") != _set_digest(snapshot.artifacts)
        or value.get("post_derived_file_count") != len(snapshot.derived)
        or value.get("post_derived_set_sha256") != _set_digest(snapshot.derived)
        or value.get("post_journal_file_count") != len(snapshot.journals)
        or value.get("post_journal_set_sha256") != _set_digest(snapshot.journals)
        or value.get("post_output_file_count") != len(snapshot.outputs)
        or value.get("post_output_set_sha256") != _set_digest(snapshot.outputs)
        or value.get("post_run_state_sha256") != _sha(snapshot.state)
    ):
        raise PrimaryResultProcessingError("processing receipt evidence changed")
    try:
        result = contract.validate_aggregate(value.get("result"), status=expected_status)
        if result != dict(expected_result):
            raise ValueError
        return result
    except (TypeError, ValueError) as error:
        raise PrimaryResultProcessingError("processing receipt invalid") from error


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _repair_linked_publication(path: Path) -> None:
    """Finish the sole safe crash state left by create-once publication."""

    staging = path.with_name(f".{path.name}.pending")
    if not os.path.lexists(staging):
        return
    try:
        _directory(path.parent, mode=0o700, owner=(ROOT_UID, ROOT_GID))
        published = path.lstat()
        pending = staging.lstat()
        if (
            not stat.S_ISREG(published.st_mode)
            or not stat.S_ISREG(pending.st_mode)
            or stat.S_ISLNK(published.st_mode)
            or stat.S_ISLNK(pending.st_mode)
            or (published.st_dev, published.st_ino) != (pending.st_dev, pending.st_ino)
            or published.st_nlink != 2
            or pending.st_nlink != 2
            or (os.name == "posix" and stat.S_IMODE(published.st_mode) != 0o600)
            or (os.name == "posix" and (published.st_uid, published.st_gid) != (ROOT_UID, ROOT_GID))
        ):
            raise OSError
        staging.unlink()
        _fsync_directory(path.parent)
    except OSError as error:
        raise PrimaryResultProcessingError("processing receipt conflict") from error


def _write_once(path: Path, value: Mapping[str, object]) -> None:
    _directory(RECEIPTS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    encoded = _canonical(value)
    if len(encoded) > MAX_RECORD_BYTES:
        raise PrimaryResultProcessingError("processing receipt too large")
    staging = path.with_name(f".{path.name}.pending")
    if os.path.lexists(path):
        _repair_linked_publication(path)
        existing, _ = _read_record(path)
        if _canonical(existing) != encoded:
            raise PrimaryResultProcessingError("processing receipt conflict")
        return
    if os.path.lexists(staging):
        pending = _stable(
            staging,
            maximum=MAX_RECORD_BYTES,
            mode=0o600,
            owner=(ROOT_UID, ROOT_GID),
        )
        if pending != encoded:
            raise PrimaryResultProcessingError("processing receipt conflict")
    else:
        descriptor = -1
        try:
            descriptor = os.open(
                staging,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            _fsync_directory(RECEIPTS_ROOT)
        except OSError as error:
            raise PrimaryResultProcessingError("processing receipt unavailable") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    try:
        os.link(staging, path, follow_symlinks=False)
        _fsync_directory(RECEIPTS_ROOT)
        staging.unlink()
        _fsync_directory(RECEIPTS_ROOT)
    except FileExistsError:
        existing, _ = _read_record(path)
        if _canonical(existing) != encoded:
            raise PrimaryResultProcessingError("processing receipt conflict") from None
        staging.unlink(missing_ok=True)
    except OSError as error:
        raise PrimaryResultProcessingError("processing receipt unavailable") from error


def _safe_env() -> dict[str, str]:
    return {
        "PATH": "/usr/sbin:/usr/bin",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _worker_args(
    request: Mapping[str, object],
    source: Path,
    runs: Path,
    snapshot: RunSnapshot,
) -> list[str]:
    environment = {
        contract.ENV_ARCHIVE_SHA256: request["archive_sha256"],
        contract.ENV_AUTHORIZATION_ID: request["authorization_id"],
        contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: _set_digest(snapshot.artifacts),
        contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256: _set_digest(snapshot.derived),
        contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: _set_digest(snapshot.journals),
        contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256: _set_digest(snapshot.outputs),
        contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: _sha(snapshot.state),
        contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: request["maximum_authorized_cost_microusd"],
        contract.ENV_RUN_ID: request["run_id"],
    }
    arguments = [
        "docker",
        "compose",
        "--progress",
        "quiet",
        "--env-file",
        os.fspath(ENV_FILE),
        "--profile",
        host.REVIEW_PRIMARY_RESULT_PROCESSING_COMPOSE_PROFILE,
        "-f",
        os.fspath(COMPOSE_PATH),
        "run",
        "--name",
        host.REVIEW_PRIMARY_RESULT_PROCESSING_CONTAINER_NAME,
        "--rm",
        "--no-TTY",
        "--no-deps",
        "--pull",
        "never",
        "--user",
        f"{WORKER_UID}:{WORKER_GID}",
    ]
    for name, value in sorted(environment.items()):
        arguments.extend(("--env", f"{name}={value}"))
    arguments.extend(
        (
            "--volume",
            f"{source.as_posix()}:{SOURCE_TARGET}:ro",
            "--volume",
            f"{runs.as_posix()}:{RUNS_TARGET}:rw",
            host.REVIEW_PRIMARY_RESULT_PROCESSING_COMPOSE_SERVICE,
        )
    )
    return [str(value) for value in arguments]


def _read_bounded(stream: IO[bytes]) -> bytes:
    try:
        return stream.read(contract.OUTPUT_MAX_BYTES + 1)
    finally:
        stream.close()


def _terminate_worker(process: subprocess.Popen[bytes]) -> None:
    if os.name != "posix" or not hasattr(os, "killpg"):
        process.kill()
        process.wait()
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=WORKER_TERMINATION_SECONDS)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    except OSError:
        process.kill()
        process.wait()


def _run_worker(
    request: Mapping[str, object], source: Path, runs: Path, snapshot: RunSnapshot
) -> dict[str, object]:
    arguments = _worker_args(request, source, runs, snapshot)
    process: subprocess.Popen[bytes] | None = None
    terminated = False
    try:
        options: dict[str, object] = {"start_new_session": True} if os.name == "posix" else {}
        process = subprocess.Popen(
            arguments,
            cwd=RELEASE_ROOT,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            **options,
        )
        if process.stdout is None or process.stderr is None:
            raise PrimaryResultProcessingError("processing worker failed")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            stdout_future = executor.submit(_read_bounded, process.stdout)
            stderr_future = executor.submit(_read_bounded, process.stderr)
            try:
                returncode = process.wait(timeout=WORKER_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as error:
                _terminate_worker(process)
                terminated = True
                stdout_future.result(timeout=5)
                stderr_future.result(timeout=5)
                raise PrimaryResultProcessingError("processing worker failed") from error
            stdout = stdout_future.result(timeout=5)
            stderr = stderr_future.result(timeout=5)
        if (
            returncode != 0
            or stderr
            or len(stdout) > contract.OUTPUT_MAX_BYTES
            or len(stderr) > contract.OUTPUT_MAX_BYTES
        ):
            raise PrimaryResultProcessingError("processing worker failed")
        try:
            return contract.parse_aggregate(stdout)
        except (TypeError, ValueError) as error:
            raise PrimaryResultProcessingError("processing worker result invalid") from error
    except PrimaryResultProcessingError:
        raise
    except (OSError, subprocess.SubprocessError, TimeoutError) as error:
        raise PrimaryResultProcessingError("processing worker failed") from error
    finally:
        if process is not None and process.poll() is None and not terminated:
            _terminate_worker(process)


def process_request(request: Mapping[str, object]) -> dict[str, object]:
    try:
        validated_request = contract.validate_request(request)
    except (TypeError, ValueError) as error:
        raise PrimaryResultProcessingError("invalid processing request") from error
    request = validated_request
    authorization_sha = _validate_authorization(request)
    try:
        prep, _ = next_submission._validate_preparation(request)
    except next_submission.NextPrimaryProcessingError as error:
        raise PrimaryResultProcessingError("preparation evidence invalid") from error
    source = _source_workspace(request)
    runs, run = _run_directory(request)
    snapshot = _read_inventory(run)
    state = _state(snapshot)
    _validate_inventory_shape(snapshot, state)
    intent_path = RECEIPTS_ROOT / f"{request['run_id']}.intent.json"
    receipt_path = RECEIPTS_ROOT / f"{request['run_id']}.json"
    _directory(RECEIPTS_ROOT, mode=0o700, owner=(ROOT_UID, ROOT_GID))
    intent_exists = os.path.lexists(intent_path)
    receipt_exists = os.path.lexists(receipt_path)
    if receipt_exists and not intent_exists:
        raise PrimaryResultProcessingError("orphan processing receipt")

    if not intent_exists:
        if receipt_exists or snapshot.derived:
            raise PrimaryResultProcessingError("processing intent missing")
        _validate_pre_state(state, prep)
        observation_intent_sha, observation_receipt_sha = _phase66_evidence(
            request, prep, snapshot, state
        )
        intent = _intent_payload(
            request,
            prep,
            authorization_sha,
            observation_intent_sha,
            observation_receipt_sha,
            snapshot,
            state,
        )
        _write_once(intent_path, intent)
    else:
        intent, _ = _read_record(intent_path)
        intent = _validate_intent(
            intent,
            request=request,
            prep=prep,
            authorization_sha=authorization_sha,
        )
        _validate_predecessor_replay(intent, request, prep, state)
        _validate_recovery(snapshot, state, intent)

    terminal = state.get("status") in {"adjudication_prepared", "completed"}
    if terminal:
        terminal_status = str(state["status"])
        result = _aggregate(request, state, terminal_status)
        if receipt_exists:
            stored, _ = _read_record(receipt_path)
            _validate_receipt(
                stored,
                intent=intent,
                snapshot=snapshot,
                expected_status=terminal_status,
                expected_result=result,
            )
            return _aggregate(request, state, "already_processed")

        # A terminal filesystem checkpoint without a receipt can be the
        # recoverable result of a crash after the worker committed its atomic
        # transition.  Re-run the provider-free worker in its idempotent mode
        # so the application-level parsers and workflow invariants validate
        # every derived artifact before root attests the recovered result.
        worker_result = _run_worker(request, source, runs, snapshot)
        after = _read_inventory(run)
        after_state = _state(after)
        _validate_inventory_shape(after, after_state)
        _original_groups_unchanged(after, intent)
        _validate_recovery(after, after_state, intent)
        expected_replay = _aggregate(request, after_state, "already_processed")
        if (
            worker_result != expected_replay
            or after != snapshot
            or after_state != state
            or _state_binding(after_state) != _state_binding(state)
        ):
            raise PrimaryResultProcessingError("processing recovery result invalid")
        try:
            if next_submission._active_binding() != (
                prep["release_sha"],
                prep["image"],
                prep["config_sha"],
            ):
                raise ValueError
        except Exception as error:
            raise PrimaryResultProcessingError("active runtime changed") from error
        _source_workspace(request)
        _write_once(receipt_path, _receipt_payload(intent, result, after))
        return result

    if receipt_exists:
        raise PrimaryResultProcessingError("processing receipt state invalid")
    _validate_pre_state(state, prep)
    _original_groups_unchanged(snapshot, intent)
    before_binding = _state_binding(state)
    worker_result = _run_worker(request, source, runs, snapshot)
    after = _read_inventory(run)
    after_state = _state(after)
    _validate_inventory_shape(after, after_state)
    _original_groups_unchanged(after, intent)
    _validate_recovery(after, after_state, intent)
    status = str(worker_result.get("status"))
    if status not in {"adjudication_prepared", "completed"}:
        raise PrimaryResultProcessingError("processing worker status invalid")
    expected = _aggregate(request, after_state, status)
    if worker_result != expected or _state_binding(after_state) != before_binding:
        raise PrimaryResultProcessingError("processing worker result invalid")
    try:
        if next_submission._active_binding() != (
            prep["release_sha"],
            prep["image"],
            prep["config_sha"],
        ):
            raise ValueError
    except Exception as error:
        raise PrimaryResultProcessingError("active runtime changed") from error
    _source_workspace(request)
    _write_once(receipt_path, _receipt_payload(intent, expected, after))
    return expected


def _require_root() -> None:
    if (
        os.name != "posix"
        or not hasattr(os, "geteuid")
        or os.geteuid() != ROOT_UID
        or os.environ.get("SUDO_USER") != host.REVIEW_USER
        or platform.system() != "Linux"
        or platform.machine() != "x86_64"
    ):
        raise PrimaryResultProcessingError("invalid processing caller")


def main() -> int:
    try:
        _require_root()
        result = process_request(_read_request(sys.stdin.buffer))
        sys.stdout.buffer.write(contract.canonical_json(result))
        return 0
    except Exception:
        sys.stderr.write("error=speaker_review_primary_result_processing_rejected\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
