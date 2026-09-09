import json
import os
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
from scripts import private_speaker_review_next_primary_submission_contract as contract
from scripts import submit_next_private_speaker_review_workspace as worker

from cinegraph.common.error_messages import SpeakerReviewErrorMessages
from cinegraph.config import DEFAULT_SPEAKER_REVIEW_CONFIGURATION
from cinegraph.config.speaker_review_submission import SUBMISSION_SCHEMA_VERSION
from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review.workflow import SpeakerReviewRunState

RUN_ID = "speaker-review-0123456789abcdef"
ARCHIVE_SHA256 = "a" * 64
AUTHORIZATION_ID = "123e4567-e89b-42d3-a456-426614174000"


def _state(
    *,
    status: SpeakerReviewRunStatus = SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED,
    completed: int = 1,
    parts: int = 2,
) -> SpeakerReviewRunState:
    batch_ids = tuple(f"batch-{index}" for index in range(1, completed + 1))
    input_file_ids = tuple(f"file-{index}" for index in range(1, completed + 1))
    return SpeakerReviewRunState(
        schema_version=5,
        run_id=RUN_ID,
        status=status,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        candidate_count=2,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        prompt_version="speaker-review-v1",
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=0.25,
        actual_primary_cost_usd=0.0,
        actual_adjudication_cost_usd=0.0,
        primary_part_count=parts,
        primary_completed_part_count=completed,
        primary_batch_id=batch_ids[-1] if completed else None,
        primary_input_file_id=input_file_ids[-1] if completed else None,
        primary_batch_ids=batch_ids,
        primary_input_file_ids=input_file_ids,
    )


def _environment() -> dict[str, str]:
    return {
        contract.ENV_ARCHIVE_SHA256: ARCHIVE_SHA256,
        contract.ENV_AUTHORIZATION_ID: AUTHORIZATION_ID,
        contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: "5000000",
        contract.ENV_RUN_ID: RUN_ID,
    }


def _write_submission_evidence(
    run_directory: Path,
    state: SpeakerReviewRunState,
    index: int,
    *,
    output: bool,
) -> Path:
    run_directory.mkdir(parents=True, exist_ok=True)
    run_directory = run_directory.resolve(strict=True)
    part = index + 1
    request = (f'{{"custom_id":"part-{part}"}}\n').encode()
    (run_directory / f"primary-part-{part:04d}-requests.jsonl").write_bytes(request)
    binding = {
        "batch_endpoint": DEFAULT_SPEAKER_REVIEW_CONFIGURATION.batch_endpoint,
        "completion_window": (
            DEFAULT_SPEAKER_REVIEW_CONFIGURATION.batch_completion_window
        ),
        "part": part,
        "prompt_version": state.prompt_version,
        "request_sha256": sha256(request).hexdigest(),
        "run_id": state.run_id,
        "schema_version": SUBMISSION_SCHEMA_VERSION,
        "stage": "primary",
    }
    stem = f".primary-part-{part:04d}-submission"
    (run_directory / f"{stem}-intent.json").write_bytes(
        (
            json.dumps(
            {"binding": binding, "status": "intent"},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
            + "\n"
        ).encode("utf-8")
    )
    (run_directory / f"{stem}-completed.json").write_bytes(
        (
            json.dumps(
                {
                    "batch_id": state.primary_batch_ids[index],
                    "binding": binding,
                    "input_file_id": state.primary_input_file_ids[index],
                    "status": "validating",
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        .encode("utf-8")
    )
    if output:
        (run_directory / f"primary-part-{part:04d}-output.jsonl").write_bytes(
            b'{"response":"bounded"}\n'
        )
    return run_directory


def test_contract_is_exact_and_aggregate_has_no_provider_fields() -> None:
    request = {
        "archive_sha256": ARCHIVE_SHA256,
        "authorization_id": AUTHORIZATION_ID,
        "maximum_authorized_cost_microusd": 5_000_000,
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "schema_version": contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
    }
    assert contract.parse_request(contract.canonical_json(request)) == request
    with pytest.raises(ValueError):
        contract.validate_request({**request, "batch_id": "private"})
    aggregate = {
        "estimated_primary_cost_microusd": 250_000,
        "operation": contract.OPERATION,
        "primary_completed_part_count": 1,
        "primary_part_count": 2,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "season_number": contract.SEASON_NUMBER,
        "status": "submitted",
        "submitted_part_count": 1,
    }
    assert contract.validate_aggregate(aggregate)["status"] == "submitted"
    with pytest.raises(ValueError):
        contract.validate_aggregate({**aggregate, "provider_batch_id": "private"})


@pytest.mark.parametrize(
    "change",
    [
        {"archive_sha256": "A" * 64},
        {"authorization_id": AUTHORIZATION_ID.upper()},
        {"maximum_authorized_cost_microusd": 0},
        {"maximum_authorized_cost_microusd": True},
        {"season_number": 1},
        {"operation": "advance"},
    ],
)
def test_contract_rejects_noncanonical_or_out_of_scope_request(
    change: dict[str, object],
) -> None:
    request: dict[str, object] = {
        "archive_sha256": ARCHIVE_SHA256,
        "authorization_id": AUTHORIZATION_ID,
        "maximum_authorized_cost_microusd": 5_000_000,
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "schema_version": contract.PROTOCOL_VERSION,
        "season_number": contract.SEASON_NUMBER,
    }

    with pytest.raises(ValueError):
        contract.validate_request({**request, **change})


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{}\r\n",
        b'{"run_id":"one","run_id":"two"}\n',
        b"x" * (contract.REQUEST_MAX_BYTES + 1),
    ],
)
def test_contract_rejects_malformed_or_oversized_wire(raw: bytes) -> None:
    with pytest.raises(ValueError):
        contract.parse_request(raw)


def test_contract_enforces_status_count_semantics() -> None:
    aggregate = {
        "estimated_primary_cost_microusd": 250_000,
        "operation": contract.OPERATION,
        "primary_completed_part_count": 1,
        "primary_part_count": 2,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "season_number": contract.SEASON_NUMBER,
        "status": "submitted",
        "submitted_part_count": 1,
    }

    with pytest.raises(ValueError):
        contract.validate_aggregate(
            {**aggregate, "primary_completed_part_count": 2}
        )
    with pytest.raises(ValueError):
        contract.validate_aggregate(
            {**aggregate, "status": "all_parts_completed", "submitted_part_count": 0}
        )


def test_completed_all_parts_is_safe_no_op_before_secret_or_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state(completed=2, parts=2)
    monkeypatch.setattr(worker, "load_validated_run_state", lambda *_: (tmp_path / RUN_ID, state))
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda *_: pytest.fail("secret read"))
    monkeypatch.setattr(worker, "_workflow", lambda *_: pytest.fail("client constructed"))
    monkeypatch.setattr(worker, "_validate_checkpoint_evidence", lambda *_: None)

    result = worker.submit_next_primary(environment=_environment(), review_root=tmp_path)

    assert result["status"] == "all_parts_completed"
    assert result["submitted_part_count"] == 0


def test_phase61_first_submission_state_is_rejected_before_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state(status=SpeakerReviewRunStatus.PRIMARY_SUBMITTED, completed=0)
    monkeypatch.setattr(worker, "load_validated_run_state", lambda *_: (tmp_path / RUN_ID, state))
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda *_: pytest.fail("secret read"))
    with pytest.raises(worker.NextPrimarySubmissionWorkerError, match="checkpoint"):
        worker.submit_next_primary(environment=_environment(), review_root=tmp_path)


def test_unhashable_replay_identifier_uses_controlled_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = replace(
        _state(status=SpeakerReviewRunStatus.PRIMARY_SUBMITTED, completed=1, parts=3),
        primary_batch_id="batch-2",
        primary_input_file_id="file-2",
        primary_batch_ids=("batch-1", {"malformed": True}),
        primary_input_file_ids=("file-1", "file-2"),
    )
    canonical = tmp_path / RUN_ID
    monkeypatch.setattr(worker, "load_validated_run_state", lambda *_: (canonical, state))
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda *_: pytest.fail("secret"))

    with pytest.raises(worker.NextPrimarySubmissionWorkerError, match="checkpoint"):
        worker.submit_next_primary(environment=_environment(), review_root=tmp_path)


def test_post_checkpoint_submission_replays_before_secret_or_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = replace(
        _state(status=SpeakerReviewRunStatus.PRIMARY_SUBMITTED, completed=1, parts=3),
        primary_batch_id="batch-2",
        primary_input_file_id="file-2",
        primary_batch_ids=("batch-1", "batch-2"),
        primary_input_file_ids=("file-1", "file-2"),
    )
    canonical = tmp_path / RUN_ID
    monkeypatch.setattr(worker, "load_validated_run_state", lambda *_: (canonical, state))
    monkeypatch.setattr(worker, "_validate_submitted_replay_evidence", lambda *_: None)
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda *_: pytest.fail("secret read"))
    monkeypatch.setattr(worker, "_workflow", lambda *_: pytest.fail("client constructed"))

    result = worker.submit_next_primary(environment=_environment(), review_root=tmp_path)

    assert result["status"] == "already_submitted"
    assert result["submitted_part_count"] == 1


def test_post_checkpoint_replay_requires_matching_completed_and_active_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = replace(
        _state(status=SpeakerReviewRunStatus.PRIMARY_SUBMITTED, completed=1, parts=3),
        primary_batch_id="batch-2",
        primary_input_file_id="file-2",
        primary_batch_ids=("batch-1", "batch-2"),
        primary_input_file_ids=("file-1", "file-2"),
    )
    canonical = _write_submission_evidence(
        tmp_path / RUN_ID,
        state,
        0,
        output=True,
    )
    _write_submission_evidence(canonical, state, 1, output=False)
    monkeypatch.setattr(worker, "load_validated_run_state", lambda *_: (canonical, state))
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda *_: pytest.fail("secret read"))
    monkeypatch.setattr(worker, "_workflow", lambda *_: pytest.fail("client constructed"))

    result = worker.submit_next_primary(environment=_environment(), review_root=tmp_path)

    assert result["status"] == "already_submitted"


def test_post_checkpoint_replay_rejects_missing_active_journal_before_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = replace(
        _state(status=SpeakerReviewRunStatus.PRIMARY_SUBMITTED, completed=1, parts=3),
        primary_batch_id="batch-2",
        primary_input_file_id="file-2",
        primary_batch_ids=("batch-1", "batch-2"),
        primary_input_file_ids=("file-1", "file-2"),
    )
    canonical = _write_submission_evidence(
        tmp_path / RUN_ID,
        state,
        0,
        output=True,
    )
    monkeypatch.setattr(worker, "load_validated_run_state", lambda *_: (canonical, state))
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda *_: pytest.fail("secret read"))

    with pytest.raises(worker.NextPrimarySubmissionWorkerError, match="evidence"):
        worker.submit_next_primary(environment=_environment(), review_root=tmp_path)


def test_cost_cap_is_enforced_before_checkpoint_evidence_or_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    canonical = tmp_path / RUN_ID
    monkeypatch.setattr(worker, "load_validated_run_state", lambda *_: (canonical, state))
    monkeypatch.setattr(worker, "_validate_checkpoint_evidence", lambda *_: pytest.fail("evidence"))
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda *_: pytest.fail("secret"))
    environment = {**_environment(), contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: "1"}

    with pytest.raises(worker.NextPrimarySubmissionWorkerError, match="cost exceeds"):
        worker.submit_next_primary(environment=environment, review_root=tmp_path)


def test_missing_checkpoint_evidence_fails_before_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    canonical = tmp_path / RUN_ID
    canonical.mkdir()
    monkeypatch.setattr(worker, "load_validated_run_state", lambda *_: (canonical, state))
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda *_: pytest.fail("secret"))

    with pytest.raises(worker.NextPrimarySubmissionWorkerError, match="evidence"):
        worker.submit_next_primary(environment=_environment(), review_root=tmp_path)


def test_completed_checkpoint_evidence_and_next_request_are_validated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    canonical = _write_submission_evidence(tmp_path / RUN_ID, state, 0, output=True)
    monkeypatch.setattr(worker, "load_validated_run_state", lambda *_: (canonical, state))
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda *_: pytest.fail("secret"))

    with pytest.raises(worker.NextPrimarySubmissionWorkerError, match="request unavailable"):
        worker.submit_next_primary(environment=_environment(), review_root=tmp_path)


def test_tampered_completed_journal_fails_before_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    canonical = _write_submission_evidence(tmp_path / RUN_ID, state, 0, output=True)
    journal = canonical / ".primary-part-0001-submission-completed.json"
    journal.write_text(journal.read_text(encoding="utf-8").replace("batch-1", "batch-x"))
    monkeypatch.setattr(worker, "load_validated_run_state", lambda *_: (canonical, state))
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda *_: pytest.fail("secret"))

    with pytest.raises(worker.NextPrimarySubmissionWorkerError, match="evidence"):
        worker.submit_next_primary(environment=_environment(), review_root=tmp_path)


def test_worker_submits_one_next_part_and_returns_only_aggregate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _state()
    after = replace(
        before,
        status=SpeakerReviewRunStatus.PRIMARY_SUBMITTED,
        primary_batch_id="batch-2",
        primary_input_file_id="file-2",
        primary_batch_ids=("batch-1", "batch-2"),
        primary_input_file_ids=("file-1", "file-2"),
    )
    canonical = tmp_path / RUN_ID
    monkeypatch.setattr(worker, "load_validated_run_state", lambda *_: (canonical, before))
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda *_: "sk-test")
    monkeypatch.setattr(worker, "_validate_checkpoint_evidence", lambda *_: None)
    canonical.mkdir(parents=True)
    (canonical / "primary-part-0002-requests.jsonl").write_bytes(b"{}\n")
    calls: list[Path] = []

    class Graph:
        def submit_next_primary(self, path: Path) -> tuple[Path, SpeakerReviewRunState]:
            calls.append(path)
            return path, after

    monkeypatch.setattr(worker, "_workflow", lambda _: Graph())
    result = worker.submit_next_primary(environment=_environment(), review_root=tmp_path)

    assert result["status"] == "submitted"
    assert result["submitted_part_count"] == 1
    assert set(result) == contract.AGGREGATE_KEYS
    assert calls == [canonical]


def test_worker_maps_exact_reconciliation_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _state()
    canonical = tmp_path / RUN_ID
    canonical.mkdir()
    (canonical / "primary-part-0002-requests.jsonl").write_bytes(b"{}\n")
    monkeypatch.setattr(worker, "load_validated_run_state", lambda *_: (canonical, before))
    monkeypatch.setattr(worker, "_validate_checkpoint_evidence", lambda *_: None)
    monkeypatch.setattr(worker, "read_stable_openai_secret", lambda *_: "sk-test")

    class Graph:
        def submit_next_primary(self, _: Path) -> tuple[Path, SpeakerReviewRunState]:
            raise RuntimeError(
                SpeakerReviewErrorMessages.BATCH_SUBMISSION_RECONCILIATION_REQUIRED
            )

    monkeypatch.setattr(worker, "_workflow", lambda _: Graph())

    result = worker.submit_next_primary(environment=_environment(), review_root=tmp_path)

    assert result["status"] == "reconciliation_required"
    assert result["submitted_part_count"] == 0


def test_worker_rejects_illegal_transition_field_mutation() -> None:
    before = _state()
    after = replace(
        before,
        status=SpeakerReviewRunStatus.PRIMARY_SUBMITTED,
        actual_primary_cost_usd=0.01,
        primary_batch_id="batch-2",
        primary_input_file_id="file-2",
        primary_batch_ids=("batch-1", "batch-2"),
        primary_input_file_ids=("file-1", "file-2"),
    )

    with pytest.raises(worker.NextPrimarySubmissionWorkerError, match="result invalid"):
        worker._validate_transition(before, after, Path("run"), Path("run"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership policy")
def test_secret_rejects_group_or_other_permissions(tmp_path: Path) -> None:
    secret = tmp_path / "openai_api_key"
    secret.write_text("sk-test", encoding="utf-8")
    secret.chmod(0o640)

    with pytest.raises(worker.NextPrimarySubmissionWorkerError, match="secret"):
        worker.read_stable_openai_secret(secret)


def test_worker_source_has_no_broad_advance_or_private_output_path() -> None:
    source = Path(worker.__file__).read_text(encoding="utf-8")

    assert ".advance(" not in source
    assert "parse_batch_results" not in source
    assert "_finalize" not in source
    assert "OPENAI_API_KEY" not in source


def test_main_suppresses_exception_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        worker,
        "submit_next_primary",
        lambda: (_ for _ in ()).throw(RuntimeError("sk-private provider payload")),
    )

    assert worker.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error=speaker_review_next_primary_submission_failed\n"
