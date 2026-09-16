from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from scripts import run_private_speaker_review_third_adjudication_observation as root

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


def _model(
    status: SpeakerReviewRunStatus = SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED,
    completed: int = 2,
) -> SpeakerReviewRunState:
    ids = tuple(f"batch-{part}" for part in range(1, completed + 2))
    inputs = tuple(f"file-{part}" for part in range(1, completed + 2))
    if status is SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED:
        ids, inputs = ids[:completed], inputs[:completed]
    return SpeakerReviewRunState(
        schema_version=5,
        run_id=RUN_ID,
        status=status,
        created_at="2026-09-13T00:00:00+00:00",
        updated_at="2026-09-13T00:00:00+00:00",
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


def _contents(model: SpeakerReviewRunState, *, observed: bool = False) -> dict[str, bytes]:
    contents = {
        root.STATE: root._canonical(model.to_dict()),
        root.REQUEST: b'{"request":"part-2"}\n',
        "candidates.jsonl": b"candidate\n",
        "source-manifest.json": b"manifest\n",
        "adjudication-part-0001-requests.jsonl": b"part-1\n",
        "adjudication-part-0002-requests.jsonl": b"part-2\n",
        ".adjudication-part-0001-submission-intent.json": b"intent-1\n",
        ".adjudication-part-0001-submission-completed.json": b"completed-1\n",
        ".adjudication-part-0002-submission-intent.json": b"intent-2\n",
        ".adjudication-part-0002-submission-completed.json": b"completed-2\n",
        "adjudication-part-0001-output.jsonl": b"output-1\n",
    }
    if observed:
        contents["adjudication-part-0002-output.jsonl"] = b"output-2\n"
    return contents


def _payload(model: SpeakerReviewRunState) -> dict[str, object]:
    payload = model.to_dict()
    payload["adjudication_batch_ids"] = list(model.adjudication_batch_ids)
    payload["adjudication_input_file_ids"] = list(model.adjudication_input_file_ids)
    return payload


def _install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    before: SpeakerReviewRunState,
    after: SpeakerReviewRunState,
    *,
    worker_status: str,
) -> tuple[list[tuple[Path, dict[str, object]]], Path]:
    run = tmp_path / RUN_ID
    run.mkdir()
    receipts = tmp_path / "third-adjudication-observation-receipts"
    receipts.mkdir()
    after_snapshot = (
        _contents(after, observed=worker_status == "observed"),
        _payload(after),
        after,
    )
    sequence = iter([(_contents(before), _payload(before), before), after_snapshot, after_snapshot])
    writes: list[tuple[Path, dict[str, object]]] = []
    monkeypatch.setattr(root, "OBSERVATION_RECEIPTS_ROOT", receipts)
    monkeypatch.setattr(root, "_directory", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(root, "_validate_authorization", lambda _: "a" * 64)
    monkeypatch.setattr(root, "_run_directory", lambda _: run)
    monkeypatch.setattr(root, "_inventory", lambda *_: next(sequence))
    monkeypatch.setattr(
        root,
        "_validate_submission_predecessor",
        lambda *_: (
            {"config_sha": "c" * 64, "image": "image@sha256:" + "9" * 64, "release_sha": "8" * 40},
            "b" * 64,
            "c" * 64,
            "d" * 64,
            500_000,
        ),
    )
    monkeypatch.setattr(root, "_validate_checkpoint_against_intent", lambda *_: None)
    monkeypatch.setattr(root, "_validate_predecessors_from_intent", lambda *_: None)
    monkeypatch.setattr(root, "_post_validate", lambda *_: None)
    monkeypatch.setattr(root.submit.phase69.phase68, "_source_workspace", lambda *_: None)
    monkeypatch.setattr(
        root.preparation,
        "_active_binding",
        lambda: ("8" * 40, "image@sha256:" + "9" * 64, "c" * 64),
    )
    monkeypatch.setattr(
        root, "_write_receipt", lambda path, value: writes.append((path, dict(value)))
    )
    estimate = 500_000
    monkeypatch.setattr(
        root,
        "_run_worker",
        lambda *_: root._aggregate(REQUEST, after.to_dict(), worker_status, estimate),
    )
    return writes, run


def test_part_three_success_writes_exact_intent_and_completion_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writes, _ = _install(
        tmp_path,
        monkeypatch,
        _model(),
        _model(SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED, 3),
        worker_status="observed",
    )
    result = root.process_request(REQUEST)
    assert result["status"] == "observed"
    assert result["adjudication_completed_part_count"] == 3
    assert [path.name for path, _ in writes] == [f"{RUN_ID}.intent.json", f"{RUN_ID}.json"]
    assert writes[0][1]["status"] == "intent"
    assert writes[1][1]["status"] == "receipt"
    assert writes[1][1]["result"] == result


def test_waiting_has_no_after_state_mutation_and_no_completion_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _model()
    writes, _ = _install(tmp_path, monkeypatch, before, before, worker_status="waiting")
    result = root.process_request(REQUEST)
    assert result["status"] == "waiting"
    assert [path.name for path, _ in writes] == [f"{RUN_ID}.intent.json"]


def test_missing_root_receipt_requires_reconciliation_without_provider_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = _model(SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED, 3)
    run = tmp_path / RUN_ID
    run.mkdir()
    receipts = tmp_path / "third-adjudication-observation-receipts"
    receipts.mkdir()
    intent_path = receipts / f"{RUN_ID}.intent.json"
    intent_path.write_bytes(b"intent\n")
    writes: list[tuple[Path, dict[str, object]]] = []
    monkeypatch.setattr(root, "OBSERVATION_RECEIPTS_ROOT", receipts)
    monkeypatch.setattr(root, "_directory", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(root, "_validate_authorization", lambda _: "a" * 64)
    monkeypatch.setattr(root, "_run_directory", lambda _: run)
    monkeypatch.setattr(
        root,
        "_inventory",
        lambda *_: (_contents(observed, observed=True), _payload(observed), observed),
    )
    monkeypatch.setattr(root, "_read_record", lambda *_: ({"status": "intent"}, "i" * 64))
    monkeypatch.setattr(
        root,
        "_validate_intent",
        lambda *_args, **_kwargs: {"estimated_adjudication_cost_microusd": 500_000},
    )
    monkeypatch.setattr(
        root, "_pre_observation_snapshot", lambda contents, state, intent: (contents, state)
    )
    monkeypatch.setattr(root, "_validate_predecessors_from_intent", lambda *_: None)
    monkeypatch.setattr(root, "_post_validate", lambda *_: None)
    monkeypatch.setattr(
        root, "_write_receipt", lambda path, value: writes.append((path, dict(value)))
    )
    monkeypatch.setattr(root, "_run_worker", lambda *_: pytest.fail("provider replay"))

    with pytest.raises(
        root.ThirdAdjudicationObservationError,
        match="reconciliation required",
    ):
        root.process_request(REQUEST)

    assert writes == []


def test_root_boundary_is_part_three_only_and_rejects_wrong_count_or_duplicate_ids() -> None:
    valid = _payload(_model())
    root._validate_submitted_state(valid)
    for invalid in (
        _payload(replace(_model(), adjudication_completed_part_count=1)),
        _payload(replace(_model(), adjudication_batch_ids=("batch-1", "batch-1"))),
        _payload(replace(_model(), adjudication_input_file_ids=("file-1", "file-1"))),
        _payload(replace(_model(), adjudication_batch_id="wrong")),
    ):
        with pytest.raises(root.ThirdAdjudicationObservationError):
            root._validate_submitted_state(invalid)


def test_inventory_binding_requires_the_exact_run_state_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _model()
    run = Path(RUN_ID)
    payload = model.to_dict()
    monkeypatch.setattr(
        root.submit,
        "_inventory",
        lambda *_: ({"run-state.json": root._canonical(payload)}, {}, {}, {}, {}, model),
    )
    monkeypatch.setattr(root, "load_validated_run_state", lambda *_: (run, model))
    monkeypatch.setattr(
        root.worker,
        "_expected_inventory_names",
        lambda *_args, **_kwargs: ({"run-state.json"}, set()),
        raising=False,
    )
    contents, state, returned = root._inventory(run)
    assert contents == {"run-state.json": root._canonical(payload)}
    assert state == model.to_dict()
    assert returned is model

    class Incomplete:
        def to_dict(self) -> dict[str, object]:
            return {"run_id": RUN_ID}

    monkeypatch.setattr(
        root.submit,
        "_inventory",
        lambda *_: ({"run-state.json": b"state"}, {}, {}, {}, {}, model),
    )
    monkeypatch.setattr(root, "load_validated_run_state", lambda *_: (run, Incomplete()))
    with pytest.raises(root.ThirdAdjudicationObservationError, match="inventory"):
        root._inventory(Path("run"))


def test_active_runtime_drift_is_rejected_after_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(tmp_path, monkeypatch, _model(), _model(), worker_status="waiting")
    monkeypatch.setattr(
        root.preparation, "_active_binding", lambda: ("drift" * 8, "other", "different")
    )
    with pytest.raises(root.ThirdAdjudicationObservationError, match="runtime"):
        root.process_request(REQUEST)


def test_worker_args_carry_request_and_all_six_verified_checkpoint_bindings(tmp_path: Path) -> None:
    binding = {
        "pre_run_state_sha256": "1" * 64,
        "pre_artifact_set_sha256": "2" * 64,
        "pre_journal_set_sha256": "3" * 64,
        "pre_output_set_sha256": "4" * 64,
        "pre_derived_set_sha256": "5" * 64,
        "request_sha256": "6" * 64,
    }
    args = root._worker_args(REQUEST, tmp_path / "review-runs", binding)
    names = {
        "pre_run_state_sha256": root.contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256,
        "pre_artifact_set_sha256": root.contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256,
        "pre_journal_set_sha256": root.contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256,
        "pre_output_set_sha256": root.contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256,
        "pre_derived_set_sha256": root.contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256,
        "request_sha256": root.contract.ENV_EXPECTED_REQUEST_SHA256,
    }
    for name, value in binding.items():
        assert f"{names[name]}={value}" in args
    assert args[-1] == root.host.REVIEW_THIRD_ADJUDICATION_OBSERVATION_COMPOSE_SERVICE


def test_container_environment_is_exactly_bound_to_request_and_image() -> None:
    binding = {
        "pre_run_state_sha256": "1" * 64,
        "pre_artifact_set_sha256": "2" * 64,
        "pre_journal_set_sha256": "3" * 64,
        "pre_output_set_sha256": "4" * 64,
        "pre_derived_set_sha256": "5" * 64,
        "request_sha256": "6" * 64,
    }
    image_environment = [
        "LANG=C.UTF-8",
        "PATH=/usr/local/bin:/usr/bin",
        "PYTHONPATH=/app/src",
    ]
    expected = {
        "LANG": "C.UTF-8",
        "PATH": "/usr/local/bin:/usr/bin",
        "PYTHONPATH": "/app/src",
        **root.WORKER_STATIC_ENVIRONMENT,
        **root._worker_environment(REQUEST, binding),
    }
    actual = [f"{name}={value}" for name, value in reversed(tuple(expected.items()))]

    assert root._container_environment_is_exact(actual, image_environment, REQUEST, binding)
    assert not root._container_environment_is_exact(
        [*actual, "HTTP_PROXY=http://unexpected.invalid"],
        image_environment,
        REQUEST,
        binding,
    )
    assert not root._container_environment_is_exact(
        [item for item in actual if not item.startswith(f"{root.contract.ENV_RUN_ID}=")],
        image_environment,
        REQUEST,
        binding,
    )


def test_main_returns_generic_error_without_private_details(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(root, "_require_root", lambda: None)
    monkeypatch.setattr(
        root,
        "_read_request",
        lambda *_: (_ for _ in ()).throw(RuntimeError("batch-private /secret/path")),
    )
    assert root.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error=speaker_review_third_adjudication_observation_rejected\n"
