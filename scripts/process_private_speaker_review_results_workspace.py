"""Process fully observed primary results without provider access.

The root boundary supplies the run identity and required digest bindings.  The
worker validates the complete, stable input inventory before invoking the
provider-free ``process_primary_results`` graph operation.  It returns only a
small aggregate and never accepts a provider credential or network adapter.
"""

from __future__ import annotations

# Imports intentionally follow the isolated release bootstrap above.
# ruff: noqa: E402, I001

import hashlib
import json
import math
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

_RELEASE_ROOT = Path(__file__).resolve().parents[1]
for _root in (_RELEASE_ROOT, _RELEASE_ROOT / "src"):
    if os.fspath(_root) not in sys.path:
        sys.path.insert(0, os.fspath(_root))

from cinegraph.adapters.workflow.langgraph.speaker_review_graph import (  # noqa: E402
    SpeakerReviewGraphWorkflow,
)
from cinegraph.config import (  # noqa: E402
    DEFAULT_MODEL_CONFIGURATION,
    DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
)
from cinegraph.domain.enums.enum import SpeakerReviewRunStatus  # noqa: E402
from cinegraph.ingestion.speaker_review.workflow import (  # noqa: E402
    SpeakerReviewRunState,
    SpeakerReviewWorkflow,
    load_validated_run_state,
)
from cinegraph.ports.llm.speaker_review_batch_gateway import (  # noqa: E402
    BatchSnapshot,
    BatchSubmission,
)
from cinegraph.ingestion.subtitle_alignment.subtitle_parser import (  # noqa: E402
    episode_key_from_subtitle_path,
)
from scripts import (
    private_speaker_review_primary_result_processing_contract as contract,
)  # noqa: E402

PRIVATE_REVIEW_RUNS_ROOT = Path("/review-workspace/review-runs")
RUN_STATE_FILENAME = "run-state.json"
RUN_STATE_MAX_BYTES = 64 * 1024
ARTIFACT_MAX_BYTES = 64 * 1024 * 1024
TOTAL_MAX_BYTES = 256 * 1024 * 1024
WORKER_UID = 10002
WORKER_GID = 10002
PRIMARY_DERIVED_NAMES = frozenset(
    {
        "primary-verdicts.jsonl",
        "primary-parse-errors.json",
        "primary-decisions.jsonl",
    }
)
_ADJUDICATION_REQUEST = re.compile(r"^adjudication-part-([0-9]{4})-requests\.jsonl$")


@dataclass(frozen=True, slots=True)
class InventorySnapshot:
    directory_identity: tuple[int, int, int, int, int]
    state: bytes
    artifacts: dict[str, bytes]
    journals: dict[str, bytes]
    outputs: dict[str, bytes]
    derived: dict[str, bytes]


class PrimaryResultProcessingWorkerError(RuntimeError):
    """Generic failure whose private details never cross the worker boundary."""


class ProviderAccessRejected(RuntimeError):
    """Raised if local result processing attempts a provider action."""


class OfflineSpeakerReviewGateway:
    """Gateway proving that this worker cannot submit, retrieve, or download."""

    def submit(
        self,
        request_filename: str,
        request_bytes: bytes,
        completion_window: str,
        metadata: dict[str, str],
    ) -> BatchSubmission:
        del request_filename, request_bytes, completion_window, metadata
        raise ProviderAccessRejected("provider access is disabled during processing")

    def retrieve(self, batch_id: str) -> BatchSnapshot:
        del batch_id
        raise ProviderAccessRejected("provider access is disabled during processing")

    def download_file(self, file_id: str) -> str:
        del file_id
        raise ProviderAccessRejected("provider access is disabled during processing")


def request_from_environment(
    environment: Mapping[str, str] | None = None,
) -> dict[str, object]:
    values = os.environ if environment is None else environment
    raw: dict[str, object] = {
        "archive_sha256": values.get(contract.ENV_ARCHIVE_SHA256, ""),
        "authorization_id": values.get(contract.ENV_AUTHORIZATION_ID, ""),
        "maximum_authorized_cost_microusd": _parse_cost(
            values.get(contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD, "")
        ),
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": values.get(contract.ENV_RUN_ID, ""),
        "schema_version": contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
    }
    try:
        return contract.validate_request(raw)
    except (TypeError, ValueError) as error:
        raise PrimaryResultProcessingWorkerError(
            "primary-result request invalid"
        ) from error


def _parse_cost(value: str) -> int:
    try:
        parsed = int(value, 10)
    except (TypeError, ValueError) as error:
        raise PrimaryResultProcessingWorkerError(
            "primary-result request invalid"
        ) from error
    if (
        str(parsed) != value
        or parsed <= 0
        or parsed > contract.MAXIMUM_AUTHORIZED_COST_MICROUSD
    ):
        raise PrimaryResultProcessingWorkerError("primary-result request invalid")
    return parsed


def _run_directory(run_id: str, review_root: Path = PRIVATE_REVIEW_RUNS_ROOT) -> Path:
    try:
        root_metadata = review_root.lstat()
        if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(
            root_metadata.st_mode
        ):
            raise OSError
        root = review_root.resolve(strict=True)
        candidate = review_root / run_id
        metadata = candidate.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise OSError
        if candidate.resolve(strict=True) != root / run_id:
            raise OSError
        if candidate.resolve(strict=True).parent != root:
            raise OSError
        return candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise PrimaryResultProcessingWorkerError(
            "primary-result run unavailable"
        ) from error


def _identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_nlink,
    )


def _validate_owner_mode(metadata: os.stat_result, *, directory: bool = False) -> None:
    if directory:
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise OSError
        expected_mode = 0o700
    else:
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_nlink != 1
        ):
            raise OSError
        expected_mode = 0o600
    if os.name == "posix" and stat.S_IMODE(metadata.st_mode) != expected_mode:
        raise OSError
    # The mounted worker directory is owned by the fixed worker identity.  On
    # non-POSIX development platforms ownership fields are not meaningful.
    if os.name == "posix" and (
        metadata.st_uid != WORKER_UID or metadata.st_gid != WORKER_GID
    ):
        raise OSError


def _read_stable(path: Path, maximum: int) -> bytes:
    descriptor = -1
    try:
        before = path.lstat()
        _validate_owner_mode(before)
        if before.st_size <= 0 or before.st_size > maximum:
            raise OSError
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        _validate_owner_mode(opened)
        if _identity(before) != _identity(opened):
            raise OSError
        raw = os.read(descriptor, maximum + 1)
        after = path.lstat()
        _validate_owner_mode(after)
        if (
            _identity(opened) != _identity(after)
            or len(raw) != opened.st_size
            or len(raw) > maximum
        ):
            raise OSError
        return raw
    except OSError as error:
        raise PrimaryResultProcessingWorkerError(
            "primary-result inventory unavailable"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _sha256(raw: bytes) -> str:
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


def _expected_digest(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise PrimaryResultProcessingWorkerError(f"primary-result {label} invalid")
    return value


def _expected_inventory(
    state: SpeakerReviewRunState,
) -> tuple[set[str], set[str], set[str], set[str], set[str]]:
    artifacts = {
        "candidates.jsonl",
        "source-manifest.json",
        *{
            f"primary-part-{part:04d}-requests.jsonl"
            for part in range(1, state.primary_part_count + 1)
        },
    }
    journals = {
        f".primary-part-{part:04d}-{kind}.json"
        for part in range(1, state.primary_part_count + 1)
        for kind in ("submission-intent", "submission-completed")
    }
    outputs = {
        f"primary-part-{part:04d}-output.jsonl"
        for part in range(1, state.primary_part_count + 1)
    }
    optional_outputs = {
        f"primary-part-{part:04d}-api-errors.jsonl"
        for part in range(1, state.primary_part_count + 1)
    }
    derived: set[str] = set()
    if state.status is SpeakerReviewRunStatus.ADJUDICATION_PREPARED:
        derived = set(PRIMARY_DERIVED_NAMES) | {
            f"adjudication-part-{part:04d}-requests.jsonl"
            for part in range(1, state.adjudication_part_count + 1)
        }
    elif state.status is SpeakerReviewRunStatus.COMPLETED:
        derived = set(PRIMARY_DERIVED_NAMES) | {
            "review-ledger.json",
            "calibration-sample.json",
        }
    return artifacts, journals, outputs, optional_outputs, derived


def _completed_output_inventory(
    source_manifest: bytes,
) -> tuple[set[str], set[str]]:
    try:
        payload = json.loads(source_manifest.decode("utf-8"))
        sources = payload.get("sources")
        if not isinstance(sources, dict) or not sources:
            raise ValueError
        files: set[str] = set()
        directories = {"reviewed"}
        for raw_name in sources:
            if not isinstance(raw_name, str) or not raw_name.endswith(
                ".script-aligned.srt"
            ):
                raise ValueError
            episode = episode_key_from_subtitle_path(Path(raw_name))
            directory = f"reviewed/season-{episode.season:02d}"
            directories.add(directory)
            reviewed_name = (
                raw_name.removesuffix(".script-aligned.srt") + ".automated-reviewed.srt"
            )
            files.add(f"{directory}/{reviewed_name}")
        return files, directories
    except (AttributeError, KeyError, TypeError, UnicodeError, ValueError) as error:
        raise PrimaryResultProcessingWorkerError(
            "primary-result source manifest invalid"
        ) from error


def _read_inventory(run_directory: Path) -> InventorySnapshot:
    try:
        directory_before = run_directory.lstat()
        _validate_owner_mode(directory_before, directory=True)
    except OSError as error:
        raise PrimaryResultProcessingWorkerError(
            "primary-result inventory unavailable"
        ) from error
    contents: dict[str, bytes] = {}
    directories: set[str] = set()
    directory_identities: dict[str, tuple[int, int, int, int, int]] = {
        ".": _identity(directory_before)
    }
    total = 0
    try:
        for current, child_directories, filenames in os.walk(
            run_directory,
            followlinks=False,
        ):
            current_path = Path(current)
            relative_directory = current_path.relative_to(run_directory).as_posix()
            if relative_directory != ".":
                directories.add(relative_directory)
                current_metadata = current_path.lstat()
                _validate_owner_mode(current_metadata, directory=True)
                directory_identities[relative_directory] = _identity(current_metadata)
            for name in child_directories:
                child = current_path / name
                child_metadata = child.lstat()
                _validate_owner_mode(child_metadata, directory=True)
                child_relative = child.relative_to(run_directory).as_posix()
                directory_identities[child_relative] = _identity(child_metadata)
            for name in filenames:
                path = current_path / name
                relative = path.relative_to(run_directory).as_posix()
                maximum = (
                    RUN_STATE_MAX_BYTES
                    if relative == RUN_STATE_FILENAME
                    else ARTIFACT_MAX_BYTES
                )
                raw = _read_stable(path, maximum)
                total += len(raw)
                if total > TOTAL_MAX_BYTES:
                    raise PrimaryResultProcessingWorkerError(
                        "primary-result inventory exceeds bound"
                    )
                contents[relative] = raw
        directory_after = run_directory.lstat()
        _validate_owner_mode(directory_after, directory=True)
        directory_identities["."] = _identity(directory_before)
        for name, identity in directory_identities.items():
            path = run_directory if name == "." else run_directory / name
            metadata = path.lstat()
            _validate_owner_mode(metadata, directory=True)
            if _identity(metadata) != identity:
                raise OSError
    except OSError as error:
        raise PrimaryResultProcessingWorkerError(
            "primary-result inventory changed"
        ) from error
    if RUN_STATE_FILENAME not in contents:
        raise PrimaryResultProcessingWorkerError("primary-result inventory changed")
    artifacts: dict[str, bytes] = {}
    journals: dict[str, bytes] = {}
    outputs: dict[str, bytes] = {}
    derived: dict[str, bytes] = {}
    for name, raw in contents.items():
        if name == RUN_STATE_FILENAME:
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
    derived.update({f"{name}/": b"" for name in directories})
    return InventorySnapshot(
        directory_identity=_identity(directory_after),
        state=contents[RUN_STATE_FILENAME],
        artifacts=artifacts,
        journals=journals,
        outputs=outputs,
        derived=derived,
    )


def _validate_inventory_shape(
    snapshot: InventorySnapshot,
    state: SpeakerReviewRunState,
) -> None:
    artifacts, journals, outputs, optional_outputs, derived = _expected_inventory(state)
    if (
        set(snapshot.artifacts) != artifacts
        or set(snapshot.journals) != journals
        or not outputs <= set(snapshot.outputs) <= outputs | optional_outputs
    ):
        raise PrimaryResultProcessingWorkerError("primary-result inventory changed")
    allowed_derived = set(derived)
    if state.status is SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED:
        for name in snapshot.derived:
            match = _ADJUDICATION_REQUEST.fullmatch(name)
            if name in PRIMARY_DERIVED_NAMES:
                allowed_derived.add(name)
            elif (
                match is not None and 1 <= int(match.group(1)) <= state.candidate_count
            ):
                allowed_derived.add(name)
    elif state.status is SpeakerReviewRunStatus.COMPLETED:
        reviewed_files, reviewed_directories = _completed_output_inventory(
            snapshot.artifacts["source-manifest.json"]
        )
        allowed_derived.update(reviewed_files)
        allowed_derived.update(f"{name}/" for name in reviewed_directories)
    if set(snapshot.derived) != allowed_derived:
        raise PrimaryResultProcessingWorkerError("primary-result inventory changed")


def _validate_inventory(
    run_directory: Path,
    state: SpeakerReviewRunState,
    environment: Mapping[str, str],
) -> InventorySnapshot:
    if (
        state.status
        not in {
            SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED,
            SpeakerReviewRunStatus.ADJUDICATION_PREPARED,
            SpeakerReviewRunStatus.COMPLETED,
        }
        or state.primary_part_count <= 0
        or state.primary_completed_part_count != state.primary_part_count
    ):
        raise PrimaryResultProcessingWorkerError(
            "primary-result run is not fully observed"
        )
    snapshot = _read_inventory(run_directory)
    _validate_inventory_shape(snapshot, state)
    expected = {
        "state": _expected_digest(
            environment.get(contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256),
            "state digest",
        ),
        "artifacts": _expected_digest(
            environment.get(contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256),
            "artifact digest",
        ),
        "journals": _expected_digest(
            environment.get(contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256),
            "journal digest",
        ),
        "outputs": _expected_digest(
            environment.get(contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256),
            "output digest",
        ),
        "derived": _expected_digest(
            environment.get(contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256),
            "derived digest",
        ),
    }
    if any(value is None for value in expected.values()):
        raise PrimaryResultProcessingWorkerError(
            "primary-result digest binding incomplete"
        )
    if (
        _sha256(snapshot.state) != expected["state"]
        or _set_digest(snapshot.artifacts) != expected["artifacts"]
        or _set_digest(snapshot.journals) != expected["journals"]
        or _set_digest(snapshot.outputs) != expected["outputs"]
        or _set_digest(snapshot.derived) != expected["derived"]
    ):
        raise PrimaryResultProcessingWorkerError(
            "primary-result digest binding changed"
        )
    return snapshot


def _workflow(maximum_authorized_cost_usd: float) -> SpeakerReviewGraphWorkflow:
    models = DEFAULT_MODEL_CONFIGURATION
    review_workflow = SpeakerReviewWorkflow(
        gateway=OfflineSpeakerReviewGateway(),
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        primary_model=models.speaker_review_model,
        adjudication_model=models.speaker_adjudication_model,
        final_review_model=models.speaker_final_review_model,
        primary_reasoning_effort=models.speaker_review_reasoning_effort,
        adjudication_reasoning_effort=models.speaker_adjudication_reasoning_effort,
        final_review_reasoning_effort=models.speaker_final_review_reasoning_effort,
        maximum_authorized_cost_usd=maximum_authorized_cost_usd,
    )
    return SpeakerReviewGraphWorkflow(review_workflow)


def _aggregate(state: SpeakerReviewRunState, *, status: str) -> dict[str, object]:
    result = {
        "accepted_by_consensus": state.accepted_by_consensus,
        "adjudication_part_count": state.adjudication_part_count,
        "candidate_count": state.candidate_count,
        "operation": contract.OPERATION,
        "primary_completed_part_count": state.primary_completed_part_count,
        "primary_part_count": state.primary_part_count,
        "purpose": contract.PURPOSE,
        "run_id": state.run_id,
        "run_status": state.status.value,
        "season_number": contract.SEASON_NUMBER,
        "status": status,
        "needs_human": state.needs_human,
    }
    try:
        return contract.validate_aggregate(result, status=status)
    except ValueError as error:
        raise PrimaryResultProcessingWorkerError(
            "primary-result aggregate invalid"
        ) from error


def _validate_transition(
    before: SpeakerReviewRunState,
    after: SpeakerReviewRunState,
    returned_directory: Path,
    canonical_directory: Path,
) -> None:
    if (
        not isinstance(after, SpeakerReviewRunState)
        or returned_directory != canonical_directory
    ):
        raise PrimaryResultProcessingWorkerError("primary-result transition invalid")
    if after.run_id != before.run_id:
        raise PrimaryResultProcessingWorkerError("primary-result transition invalid")
    allowed_statuses = {
        SpeakerReviewRunStatus.ADJUDICATION_PREPARED,
        SpeakerReviewRunStatus.COMPLETED,
    }
    if after.status not in allowed_statuses:
        raise PrimaryResultProcessingWorkerError("primary-result transition invalid")
    before_payload = before.to_dict()
    after_payload = after.to_dict()
    allowed = {
        "status",
        "updated_at",
        "actual_primary_cost_usd",
        "actual_total_cost_usd",
        "accepted_by_consensus",
        "adjudication_part_count",
        "needs_human",
    }
    if any(
        before_payload[field] != after_payload[field]
        for field in before_payload.keys() - allowed
    ):
        raise PrimaryResultProcessingWorkerError("primary-result transition invalid")


def _validate_post_inventory(
    run_directory: Path,
    before: InventorySnapshot,
    after_state: SpeakerReviewRunState,
) -> InventorySnapshot:
    after = _read_inventory(run_directory)
    _validate_inventory_shape(after, after_state)
    if (
        after.artifacts != before.artifacts
        or after.journals != before.journals
        or after.outputs != before.outputs
        or any(after.derived.get(name) != raw for name, raw in before.derived.items())
    ):
        raise PrimaryResultProcessingWorkerError(
            "primary-result immutable evidence changed"
        )
    try:
        persisted = json.loads(after.state.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PrimaryResultProcessingWorkerError(
            "primary-result transition invalid"
        ) from error
    expected_persisted = json.loads(
        json.dumps(after_state.to_dict(), ensure_ascii=False, sort_keys=True)
    )
    if persisted != expected_persisted:
        raise PrimaryResultProcessingWorkerError("primary-result transition invalid")
    return after


def process_primary_results(
    *,
    environment: Mapping[str, str] | None = None,
    review_root: Path = PRIVATE_REVIEW_RUNS_ROOT,
) -> dict[str, object]:
    values = os.environ if environment is None else environment
    request = request_from_environment(environment)
    run_directory = _run_directory(str(request["run_id"]), review_root)
    try:
        canonical, state = load_validated_run_state(
            run_directory, DEFAULT_SPEAKER_REVIEW_CONFIGURATION
        )
    except Exception as error:
        raise PrimaryResultProcessingWorkerError(
            "primary-result run unavailable"
        ) from error
    if canonical != run_directory:
        raise PrimaryResultProcessingWorkerError("primary-result run unavailable")
    before_inventory = _validate_inventory(canonical, state, values)
    before = state
    maximum_authorized_cost_usd = (
        int(request["maximum_authorized_cost_microusd"]) / 1_000_000
    )
    if (
        not math.isfinite(maximum_authorized_cost_usd)
        or state.estimated_primary_cost_usd > maximum_authorized_cost_usd
        or state.actual_primary_cost_usd > maximum_authorized_cost_usd
    ):
        raise PrimaryResultProcessingWorkerError(
            "primary-result authorization insufficient"
        )
    try:
        returned_directory, processed = _workflow(
            maximum_authorized_cost_usd
        ).process_primary_results(canonical, verified_run_state=before)
    except Exception as error:
        raise PrimaryResultProcessingWorkerError(
            "primary-result processing failed"
        ) from error
    _validate_transition(before, processed, returned_directory, canonical)
    _validate_post_inventory(canonical, before_inventory, processed)
    status = (
        "already_processed"
        if before.status
        in {
            SpeakerReviewRunStatus.ADJUDICATION_PREPARED,
            SpeakerReviewRunStatus.COMPLETED,
        }
        else processed.status.value
    )
    return _aggregate(processed, status=status)


def main() -> int:
    try:
        result = process_primary_results()
        sys.stdout.buffer.write(contract.canonical_json(result))
        return 0
    except PrimaryResultProcessingWorkerError:
        sys.stderr.write("error=speaker_review_primary_result_processing_failed\n")
        return 2
    except Exception:
        sys.stderr.write("error=speaker_review_primary_result_processing_failed\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
