from __future__ import annotations

from pathlib import Path

import pytest
from scripts import run_private_speaker_review_next_adjudication as root

from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review.workflow import SpeakerReviewRunState

RUN_ID = "speaker-review-0123456789abcdef"
AUTHORIZATION_ID = "123e4567-e89b-42d3-a456-426614174000"
REQUEST = {
    "archive_sha256": "a" * 64,
    "authorization_id": AUTHORIZATION_ID,
    "maximum_authorized_cost_microusd": 5_000_000,
    "operation": root.contract.OPERATION,
    "purpose": root.contract.PURPOSE,
    "run_id": RUN_ID,
    "schema_version": root.contract.PROTOCOL_VERSION,
    "season_number": root.contract.SEASON_NUMBER,
}


def _state(
    status: SpeakerReviewRunStatus = SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED,
    *,
    completed: int = 1,
) -> SpeakerReviewRunState:
    submitted = status is SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED
    ids = tuple(f"batch-{part}" for part in range(1, completed + 1))
    inputs = tuple(f"file-{part}" for part in range(1, completed + 1))
    if submitted:
        ids = (*ids, f"batch-{completed + 1}")
        inputs = (*inputs, f"file-{completed + 1}")
    return SpeakerReviewRunState(
        schema_version=5,
        run_id=RUN_ID,
        status=status,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:01:00+00:00" if submitted else "2026-01-01T00:00:00+00:00",
        candidate_count=2,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        prompt_version="speaker-review-v1",
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=0.25,
        actual_primary_cost_usd=0.10,
        actual_adjudication_cost_usd=0.0,
        primary_part_count=1,
        primary_completed_part_count=1,
        adjudication_part_count=3,
        adjudication_completed_part_count=completed,
        adjudication_batch_id=ids[-1],
        adjudication_input_file_id=inputs[-1],
        adjudication_batch_ids=ids,
        adjudication_input_file_ids=inputs,
    )


def _files(state: SpeakerReviewRunState) -> dict[str, bytes]:
    value = {
        root.STATE_NAME: root._canonical(state.to_dict()),
        "adjudication-part-0002-requests.jsonl": b'{"body":{}}\n',
        "adjudication-part-0001-output.jsonl": b"output-1\n",
    }
    if state.status is SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED:
        value[root._INTENT_NAME.format(part=2)] = b"intent-2\n"
        value[root._COMPLETED_NAME.format(part=2)] = b"completed-2\n"
    return value


def _inventory_tuple(
    state: SpeakerReviewRunState,
) -> tuple[
    dict[str, bytes],
    dict[str, bytes],
    dict[str, bytes],
    dict[str, bytes],
    dict[str, bytes],
    SpeakerReviewRunState,
]:
    files = _files(state)
    artifacts, journals, outputs, derived = root._classes(files)
    return files, artifacts, journals, outputs, derived, state


def _install_process_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inventories: list[tuple[dict[str, bytes], dict[str, bytes], dict[str, bytes], dict[str, bytes], dict[str, bytes], SpeakerReviewRunState]],
) -> tuple[Path, list[tuple[Path, dict[str, object]]]]:
    run = tmp_path / RUN_ID
    run.mkdir()
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    sequence = iter(inventories)
    writes: list[tuple[Path, dict[str, object]]] = []
    preparation = {
        "config_sha": "c" * 64,
        "image": "ghcr.io/captainvc/cinegraph@sha256:" + "9" * 64,
        "release_sha": "8" * 40,
    }
    monkeypatch.setattr(root, "RECEIPTS_ROOT", receipts)
    monkeypatch.setattr(root, "_validate_authorization", lambda _: "1" * 64)
    monkeypatch.setattr(root, "_run_directory", lambda _: run)
    monkeypatch.setattr(root, "_inventory", lambda *_: next(sequence))
    monkeypatch.setattr(root, "_validate_state_shape", lambda *_: None)
    monkeypatch.setattr(root, "_validate_completed_evidence", lambda *_: None)
    monkeypatch.setattr(
        root,
        "_validate_phase70_predecessor",
        lambda *_: (preparation, "2" * 64, "3" * 64, "4" * 64, 200_000, 100_000),
    )
    monkeypatch.setattr(root, "_estimate_cost", lambda *_: 200_000)
    monkeypatch.setattr(root.worker, "_validate_replay_evidence", lambda *_: None)
    monkeypatch.setattr(
        root.preparation,
        "_active_binding",
        lambda: (preparation["release_sha"], preparation["image"], preparation["config_sha"]),
    )
    monkeypatch.setattr(
        root,
        "_write_once",
        lambda path, value: writes.append((path, dict(value))),
    )
    return receipts, writes


def test_host_boundary_is_limited_to_phase70_authenticated_part_two() -> None:
    root._validate_state_shape(_state())
    with pytest.raises(root.NextAdjudicationSubmissionError, match="predecessor"):
        root._validate_state_shape(_state(completed=2))


def test_worker_arguments_bind_request_and_all_six_digests(tmp_path: Path) -> None:
    bindings = {
        root.contract.ENV_EXPECTED_REQUEST_SHA256: "1" * 64,
        root.contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: "2" * 64,
        root.contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: "3" * 64,
        root.contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: "4" * 64,
        root.contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256: "5" * 64,
        root.contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256: "6" * 64,
    }

    arguments = root._worker_args(REQUEST, tmp_path / "review-runs", bindings)

    for name, value in bindings.items():
        assert f"{name}={value}" in arguments
    assert arguments[-1] == root.host.REVIEW_NEXT_ADJUDICATION_COMPOSE_SERVICE
    assert (
        f"{(tmp_path / 'review-runs').as_posix()}:{root.host.REVIEW_NEXT_ADJUDICATION_RUNS_TARGET}:rw"
        in arguments
    )


def test_fresh_process_submits_once_and_writes_intent_then_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _state()
    after = _state(SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED)
    _, writes = _install_process_dependencies(
        tmp_path,
        monkeypatch,
        [_inventory_tuple(before), _inventory_tuple(after)],
    )
    calls = 0

    def run_worker(*_: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return root._aggregate(
            REQUEST, after, status="submitted", estimated=200_000, submitted=1
        )

    monkeypatch.setattr(root, "_run_worker", run_worker)

    result = root.process_request(REQUEST)

    assert calls == 1
    assert result["status"] == "submitted"
    assert [path.name for path, _ in writes] == [f"{RUN_ID}.intent.json", f"{RUN_ID}.json"]
    assert writes[0][1]["status"] == "intent"
    assert writes[1][1]["status"] == "receipt"


def test_submitted_checkpoint_repairs_missing_root_receipt_provider_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submitted = _state(SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED)
    receipts, writes = _install_process_dependencies(
        tmp_path,
        monkeypatch,
        [_inventory_tuple(submitted), _inventory_tuple(submitted)],
    )
    intent_path = receipts / f"{RUN_ID}.intent.json"
    intent_path.write_bytes(b"present")
    expected_intent = {"status": "intent", "binding": "exact"}
    monkeypatch.setattr(root, "_record", lambda *_: (expected_intent, "f" * 64))
    monkeypatch.setattr(root, "_validate_binding", lambda *_: expected_intent)
    monkeypatch.setattr(root, "_pre_submission_snapshot", lambda files, state, _: (files, state))
    monkeypatch.setattr(root, "_binding", lambda *_args, **_kwargs: expected_intent)
    monkeypatch.setattr(
        root,
        "_run_worker",
        lambda *_: root._aggregate(
            REQUEST,
            submitted,
            status="already_submitted",
            estimated=200_000,
            submitted=1,
        ),
    )

    result = root.process_request(REQUEST)

    assert result["status"] == "already_submitted"
    assert [path.name for path, _ in writes] == [f"{RUN_ID}.json"]


def test_replay_rejects_stale_root_intent_before_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submitted = _state(SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED)
    receipts, _ = _install_process_dependencies(
        tmp_path, monkeypatch, [_inventory_tuple(submitted)]
    )
    (receipts / f"{RUN_ID}.intent.json").write_bytes(b"present")
    stale = {"status": "intent", "binding": "stale"}
    expected = {"status": "intent", "binding": "current"}
    monkeypatch.setattr(root, "_record", lambda *_: (stale, "f" * 64))
    monkeypatch.setattr(root, "_validate_binding", lambda *_: stale)
    monkeypatch.setattr(root, "_pre_submission_snapshot", lambda files, state, _: (files, state))
    monkeypatch.setattr(root, "_binding", lambda *_args, **_kwargs: expected)
    monkeypatch.setattr(root, "_run_worker", lambda *_: pytest.fail("worker must stay closed"))

    with pytest.raises(root.NextAdjudicationSubmissionError, match="binding changed"):
        root.process_request(REQUEST)


def test_worker_result_and_post_inventory_are_fail_closed() -> None:
    before = _state()
    after = _state(SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED)
    valid = root._aggregate(
        REQUEST, after, status="submitted", estimated=200_000, submitted=1
    )
    with pytest.raises(root.NextAdjudicationSubmissionError, match="worker result"):
        root._validate_worker_result(
            {**valid, "estimated_adjudication_cost_microusd": 200_001},
            request=REQUEST,
            before=before,
            status="submitted",
            estimated=200_000,
            actual=100_000,
        )

    before_files = _files(before)
    after_files = _files(after)
    after_files["unexpected-private-file"] = b"tamper\n"
    with pytest.raises(root.NextAdjudicationSubmissionError, match="inventory"):
        root._post_validate(
            before_files,
            after_files,
            before,
            after,
            result_status="submitted",
        )
