from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from scripts import (
    private_speaker_review_fourth_adjudication_submission_contract as phase75_contract,
)
from scripts import run_private_speaker_review_fourth_adjudication_observation as root

from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review.workflow import SpeakerReviewRunState

RUN_ID = "speaker-review-0123456789abcdef"
REQUEST = {
    "archive_sha256": "a" * 64,
    "authorization_id": "123e4567-e89b-42d3-a456-426614174000",
    "maximum_authorized_cost_microusd": 5_000_000,
    "operation": root.contract.OPERATION,
    "purpose": root.contract.PURPOSE,
    "run_id": RUN_ID,
    "schema_version": root.contract.PROTOCOL_VERSION,
    "season_number": root.contract.SEASON_NUMBER,
}


def _state(status: SpeakerReviewRunStatus = SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED, count: int = 3) -> SpeakerReviewRunState:
    return SpeakerReviewRunState(
        schema_version=5, run_id=RUN_ID, status=status,
        created_at="2026-09-16T00:00:00+00:00", updated_at="2026-09-16T00:00:01+00:00",
        candidate_count=2, primary_model="gpt-5.6-luna", adjudication_model="gpt-5.6-terra",
        prompt_version="speaker-review-v1", maximum_cost_usd=5.0,
        estimated_primary_cost_usd=0.25, actual_primary_cost_usd=0.10,
        actual_adjudication_cost_usd=0.0, primary_part_count=1, primary_completed_part_count=1,
        adjudication_part_count=4, adjudication_completed_part_count=count,
        adjudication_batch_id="batch-4", adjudication_input_file_id="file-4",
        adjudication_batch_ids=tuple(f"batch-{n}" for n in range(1, 5)),
        adjudication_input_file_ids=tuple(f"file-{n}" for n in range(1, 5)),
    )


def _payload(model: SpeakerReviewRunState) -> dict[str, object]:
    value = model.to_dict()
    value["adjudication_batch_ids"] = list(model.adjudication_batch_ids)
    value["adjudication_input_file_ids"] = list(model.adjudication_input_file_ids)
    return value


def _install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, worker_status: str,
    after_status: SpeakerReviewRunStatus | None = None, after_count: int | None = None,
) -> tuple[list[tuple[Path, dict[str, object]]], dict[str, object]]:
    before = _state()
    after = _state(after_status or before.status, after_count if after_count is not None else before.adjudication_completed_part_count)
    run = tmp_path / RUN_ID
    run.mkdir()
    receipts = tmp_path / "fourth-adjudication-observation-receipts"
    receipts.mkdir()
    contents = {
        root.STATE: root._canonical(_payload(before)),
        root.REQUEST: b'{"part":4}\n',
        "candidates.jsonl": b"candidate\n",
        "source-manifest.json": b"manifest\n",
    }
    writes: list[tuple[Path, dict[str, object]]] = []
    monkeypatch.setattr(root, "OBSERVATION_RECEIPTS_ROOT", receipts)
    monkeypatch.setattr(root, "_directory", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(root, "_validate_authorization", lambda _: "a" * 64)
    monkeypatch.setattr(root, "_run_directory", lambda _: run)
    monkeypatch.setattr(root, "_inventory", lambda *_: (contents, _payload(before), before))
    monkeypatch.setattr(root, "_validate_submission_predecessor", lambda *_: (
        {"config_sha": "c" * 64, "image": "image@sha256:" + "9" * 64, "release_sha": "8" * 40},
        "b" * 64, "c" * 64, "d" * 64, 500_000,
    ))
    monkeypatch.setattr(root, "_root_binding", lambda *_: {
        key: value for key, value in {
            "archive_sha256": REQUEST["archive_sha256"], "actual_primary_cost_microusd": 100_000,
            "adjudication_part_count": 4, "adjudication_completed_part_count": 3,
            "observed_part_number": 4, "active_batch_id": "batch-3", "active_input_file_id": "file-3",
            "authorization_id": REQUEST["authorization_id"], "authorization_sha256": "a" * 64,
            "configuration_sha256": "c" * 64, "estimated_adjudication_cost_microusd": 500_000,
            "fourth_adjudication_submission_intent_sha256": "c" * 64,
            "fourth_adjudication_submission_receipt_sha256": "d" * 64,
            "image_reference": "image@sha256:" + "9" * 64, "maximum_authorized_cost_microusd": 5_000_000,
            "operation": root.contract.OPERATION, "pre_artifact_set_sha256": "1" * 64,
            "pre_derived_set_sha256": "2" * 64, "pre_journal_set_sha256": "3" * 64,
            "pre_output_set_sha256": "4" * 64, "pre_run_state_sha256": "5" * 64,
            "pre_state_binding_sha256": "6" * 64, "prep_receipt_sha256": "7" * 64,
            "purpose": root.contract.PURPOSE, "release_sha": "8" * 40, "request_sha256": "9" * 64,
            "run_id": RUN_ID, "schema_version": 1, "season_number": 2, "status": "intent",
            "pre_updated_at": before.updated_at,
        }.items()
    })
    monkeypatch.setattr(root, "_validate_checkpoint_against_intent", lambda *_: None)
    monkeypatch.setattr(root, "_validate_predecessors_from_intent", lambda *_: None)
    monkeypatch.setattr(root, "_post_validate", lambda *_: None)
    monkeypatch.setattr(root, "_post_inventory", lambda *_: (
        {**contents, **({"adjudication-part-0004-output.jsonl": b"out\n"} if worker_status == "observed" else {})},
        _payload(after), after,
    ))
    monkeypatch.setattr(root.preparation, "_active_binding", lambda: ("8" * 40, "image@sha256:" + "9" * 64, "c" * 64))
    monkeypatch.setattr(root.submit.phase69.phase68, "_source_workspace", lambda *_: None)
    monkeypatch.setattr(root, "_write_receipt", lambda path, value: writes.append((path, dict(value))))
    monkeypatch.setattr(root, "_run_worker", lambda *_: root._aggregate(REQUEST, _payload(after), worker_status, 500_000))
    return writes, _payload(after)


@pytest.mark.parametrize(
    ("status", "after_status", "after_count"),
    [("observed", SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED, 4),
     ("waiting", SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED, 3),
     ("failed", SpeakerReviewRunStatus.FAILED, 3),
     ("reconciliation_required", SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED, 3)],
)
def test_fresh_observation_paths_publish_only_expected_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str,
    after_status: SpeakerReviewRunStatus, after_count: int,
) -> None:
    writes, after = _install(tmp_path, monkeypatch, worker_status=status, after_status=after_status, after_count=after_count)
    result = root.process_request(REQUEST)
    assert result["status"] == status
    assert result["adjudication_completed_part_count"] == (4 if status == "observed" else 3)
    assert [path.name for path, _ in writes] == ([f"{RUN_ID}.intent.json", f"{RUN_ID}.json"] if status in {"observed", "failed"} else [f"{RUN_ID}.intent.json"])
    if status in {"observed", "failed"}:
        assert writes[-1][1]["status"] == "receipt"
        assert writes[-1][1]["result"] == result
    assert after["adjudication_part_count"] == 4


def test_submitted_checkpoint_requires_exactly_three_completed_and_four_unique_ids() -> None:
    valid = _payload(_state())
    root._validate_submitted_state(valid)
    cases = [
        replace(_state(), adjudication_completed_part_count=2),
        replace(_state(), adjudication_batch_ids=("batch-1", "batch-2", "batch-3", "batch-3")),
        replace(_state(), adjudication_input_file_ids=("file-1", "file-2", "file-3", "file-3")),
        replace(_state(), adjudication_batch_id="wrong"),
    ]
    for invalid in cases:
        with pytest.raises(root.FourthAdjudicationObservationError):
            root._validate_submitted_state(_payload(invalid))


def test_part_four_output_or_api_error_is_never_preexisting() -> None:
    assert root.OBSERVATION_OUTPUTS == {
        "adjudication-part-0004-output.jsonl", "adjudication-part-0004-api-errors.jsonl"
    }
    assert root.TERMINAL_OUTPUT not in root.OBSERVATION_OUTPUTS


def test_phase75_predecessor_request_is_authenticated_exactly() -> None:
    intent = {
        "authorization_id": REQUEST["authorization_id"],
        "maximum_authorized_cost_microusd": 5_000_000,
    }
    expected = {
        "archive_sha256": REQUEST["archive_sha256"],
        "authorization_id": REQUEST["authorization_id"],
        "maximum_authorized_cost_microusd": 5_000_000,
        "operation": phase75_contract.OPERATION,
        "purpose": phase75_contract.PURPOSE,
        "run_id": RUN_ID,
        "schema_version": phase75_contract.PROTOCOL_VERSION,
        "season_number": phase75_contract.SEASON_NUMBER,
    }
    raw = phase75_contract.canonical_json(expected)
    # Isolate this unit from root-only filesystem ownership checks; the
    # contract parser and exact request binding remain real.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(root, "_stable", lambda *_args, **_kwargs: raw)
    try:
        actual, actual_raw, digest = root._phase75_request(REQUEST, intent)
        assert actual == expected and actual_raw == raw and digest == root._sha(raw)
        bad = {**intent, "maximum_authorized_cost_microusd": 4_000_000}
        with pytest.raises(ValueError):
            root._phase75_request(REQUEST, bad)
    finally:
        monkeypatch.undo()


def test_missing_success_receipt_requires_reconciliation_without_provider_or_fabrication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = _state(SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED, 4)
    run = tmp_path / RUN_ID
    run.mkdir()
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    (receipts / f"{RUN_ID}.intent.json").write_bytes(b"intent\n")
    monkeypatch.setattr(root, "OBSERVATION_RECEIPTS_ROOT", receipts)
    monkeypatch.setattr(root, "_validate_authorization", lambda _: "a" * 64)
    monkeypatch.setattr(root, "_run_directory", lambda _: run)
    monkeypatch.setattr(root, "_directory", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(root, "_inventory", lambda *_: ({root.STATE: b"state"}, _payload(observed), observed))
    monkeypatch.setattr(root, "_read_record", lambda *_: ({"status": "intent"}, "i" * 64))
    monkeypatch.setattr(root, "_validate_intent", lambda *_args, **_kwargs: {"estimated_adjudication_cost_microusd": 500_000})
    monkeypatch.setattr(root, "_pre_observation_snapshot", lambda contents, state, intent: (contents, state))
    monkeypatch.setattr(root, "_validate_predecessors_from_intent", lambda *_: None)
    monkeypatch.setattr(root, "_post_validate", lambda *_: None)
    monkeypatch.setattr(root, "_run_worker", lambda *_: pytest.fail("provider replay"))
    with pytest.raises(root.FourthAdjudicationObservationError, match="reconciliation required"):
        root.process_request(REQUEST)


def test_already_observed_replay_is_provider_free_and_authenticates_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = _state(SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED, 4)
    run = tmp_path / RUN_ID
    run.mkdir()
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    for name in (f"{RUN_ID}.intent.json", f"{RUN_ID}.json"):
        (receipts / name).write_bytes(b"record\n")
    monkeypatch.setattr(root, "OBSERVATION_RECEIPTS_ROOT", receipts)
    monkeypatch.setattr(root, "_validate_authorization", lambda _: "a" * 64)
    monkeypatch.setattr(root, "_run_directory", lambda _: run)
    monkeypatch.setattr(root, "_directory", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(root, "_inventory", lambda *_: ({root.STATE: b"state"}, _payload(observed), observed))
    intent = {"estimated_adjudication_cost_microusd": 500_000}
    monkeypatch.setattr(root, "_read_record", lambda path: (intent, "i" * 64) if path.name.endswith("intent.json") else ({}, "r" * 64))
    monkeypatch.setattr(root, "_validate_intent", lambda *_args, **_kwargs: intent)
    monkeypatch.setattr(root, "_pre_observation_snapshot", lambda contents, state, intent: (contents, state))
    monkeypatch.setattr(root, "_validate_predecessors_from_intent", lambda *_: None)
    monkeypatch.setattr(root, "_post_validate", lambda *_: None)
    monkeypatch.setattr(root, "_validate_final_receipt", lambda *args, **kwargs: None)
    monkeypatch.setattr(root, "_run_worker", lambda *_: pytest.fail("provider replay"))
    result = root.process_request(REQUEST)
    assert result["status"] == "already_observed"


def test_final_and_pending_hard_links_are_repaired_before_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = _state(SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED, 4)
    run = tmp_path / RUN_ID
    run.mkdir()
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    intent_path = receipts / f"{RUN_ID}.intent.json"
    receipt_path = receipts / f"{RUN_ID}.json"
    intent_path.write_bytes(b"record\n")
    receipt_path.write_bytes(b"record\n")
    (receipts / f".{intent_path.name}.pending").write_bytes(b"pending\n")
    (receipts / f".{receipt_path.name}.pending").write_bytes(b"pending\n")
    monkeypatch.setattr(root, "OBSERVATION_RECEIPTS_ROOT", receipts)
    monkeypatch.setattr(root, "_validate_authorization", lambda _: "a" * 64)
    monkeypatch.setattr(root, "_run_directory", lambda _: run)
    monkeypatch.setattr(root, "_directory", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(root, "_inventory", lambda *_: ({root.STATE: b"state"}, _payload(observed), observed))
    intent = {"estimated_adjudication_cost_microusd": 500_000}
    monkeypatch.setattr(root, "_read_record", lambda path: (intent, "i" * 64) if path.name.endswith("intent.json") else ({}, "r" * 64))
    monkeypatch.setattr(root, "_validate_intent", lambda *_args, **_kwargs: intent)
    monkeypatch.setattr(root, "_pre_observation_snapshot", lambda contents, state, intent: (contents, state))
    monkeypatch.setattr(root, "_validate_predecessors_from_intent", lambda *_: None)
    monkeypatch.setattr(root, "_post_validate", lambda *_: None)
    monkeypatch.setattr(root, "_validate_final_receipt", lambda *args, **kwargs: None)
    monkeypatch.setattr(root, "_run_worker", lambda *_: pytest.fail("provider replay"))
    repaired: list[str] = []
    def repair(path: Path) -> None:
        repaired.append(path.name)
        path.with_name(f".{path.name}.pending").unlink()
    monkeypatch.setattr(root.submit, "_repair_linked_publication", repair)
    result = root.process_request(REQUEST)
    assert result["status"] == "already_observed"
    assert repaired == [intent_path.name, receipt_path.name]


@pytest.mark.parametrize("pending", ["intent", "receipt"])
def test_pending_receipt_evidence_is_ambiguous_and_provider_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pending: str,
) -> None:
    writes, _ = _install(tmp_path, monkeypatch, worker_status="waiting")
    receipts = root.OBSERVATION_RECEIPTS_ROOT
    pending_name = f".{RUN_ID}.intent.json.pending" if pending == "intent" else f".{RUN_ID}.json.pending"
    (receipts / pending_name).write_bytes(b"pending\n")
    if pending == "intent":
        result = root.process_request(REQUEST)
        assert result["status"] == "waiting"
        assert [path.name for path, _ in writes] == [f"{RUN_ID}.intent.json"]
    else:
        with pytest.raises(root.FourthAdjudicationObservationError, match="ambiguous"):
            root.process_request(REQUEST)
        assert writes == []


def test_main_error_is_generic_and_never_leaks_id_or_path(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(root, "_require_root", lambda: None)
    monkeypatch.setattr(root, "_read_request", lambda *_: (_ for _ in ()).throw(RuntimeError("batch-private /secret")))
    assert root.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error=speaker_review_fourth_adjudication_observation_rejected\n"
