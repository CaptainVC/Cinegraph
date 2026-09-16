from __future__ import annotations

from pathlib import Path

import pytest
from scripts import run_private_speaker_review_third_adjudication as root

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
    completed: int = 2,
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
        updated_at=("2026-01-01T00:01:00+00:00" if submitted else "2026-01-01T00:00:00+00:00"),
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
        **{
            f"adjudication-part-{part:04d}-requests.jsonl": b'{"body":{}}\n' for part in range(1, 4)
        },
        "adjudication-part-0001-output.jsonl": b"output-1\n",
        "adjudication-part-0002-output.jsonl": b"output-2\n",
    }
    if state.status is SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED:
        value[root._INTENT_NAME.format(part=3)] = b"intent-3\n"
        value[root._COMPLETED_NAME.format(part=3)] = b"completed-3\n"
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
    inventories: list[
        tuple[
            dict[str, bytes],
            dict[str, bytes],
            dict[str, bytes],
            dict[str, bytes],
            dict[str, bytes],
            SpeakerReviewRunState,
        ]
    ],
) -> tuple[Path, list[tuple[Path, dict[str, object]]]]:
    run = tmp_path / RUN_ID
    run.mkdir()
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    sequence = iter(
        [inventories[0], *(item for snapshot in inventories[1:] for item in (snapshot, snapshot))]
    )
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
        "_validate_phase72_predecessor",
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


def test_host_boundary_is_limited_to_phase72_authenticated_part_three() -> None:
    root._validate_state_shape(_state())
    with pytest.raises(root.ThirdAdjudicationSubmissionError, match="checkpoint|predecessor"):
        root._validate_state_shape(_state(completed=1))


def test_worker_arguments_bind_exact_run_and_all_six_digests(tmp_path: Path) -> None:
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
    assert arguments[-1] == root.host.REVIEW_THIRD_ADJUDICATION_COMPOSE_SERVICE
    run_id = str(REQUEST["run_id"])
    assert (
        f"{(tmp_path / 'review-runs' / run_id).as_posix()}:"
        f"{(root.host.REVIEW_THIRD_ADJUDICATION_RUNS_TARGET / run_id).as_posix()}:rw" in arguments
    )


def test_container_environment_is_exactly_bound_to_request_and_image() -> None:
    bindings = {
        root.contract.ENV_EXPECTED_REQUEST_SHA256: "1" * 64,
        root.contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256: "2" * 64,
        root.contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256: "3" * 64,
        root.contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256: "4" * 64,
        root.contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256: "5" * 64,
        root.contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256: "6" * 64,
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
        **root._worker_environment(REQUEST, bindings),
    }
    actual = [f"{name}={value}" for name, value in reversed(tuple(expected.items()))]

    assert root._container_environment_is_exact(actual, image_environment, REQUEST, bindings)
    assert not root._container_environment_is_exact(
        [*actual, "HTTP_PROXY=http://unexpected.invalid"],
        image_environment,
        REQUEST,
        bindings,
    )
    assert not root._container_environment_is_exact(
        [item for item in actual if not item.startswith(f"{root.contract.ENV_RUN_ID}=")],
        image_environment,
        REQUEST,
        bindings,
    )


def test_root_record_repairs_completed_hard_link_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = tmp_path / "receipt.json"
    raw = root._canonical({"status": "receipt"})
    repaired: list[Path] = []
    monkeypatch.setattr(
        root,
        "_repair_linked_publication",
        lambda path: repaired.append(path),
    )
    monkeypatch.setattr(root, "_stable", lambda *_args, **_kwargs: raw)

    value, digest = root._record(published)

    assert value == {"status": "receipt"}
    assert digest == root._sha(raw)
    assert repaired == [published]


def test_root_inventory_rejects_empty_derived_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    run = tmp_path / RUN_ID
    run.mkdir()
    raw_state = root._canonical(state.to_dict())
    (run / root.STATE_NAME).write_bytes(raw_state)
    files = {root.STATE_NAME: raw_state}
    monkeypatch.setattr(
        root.phase69,
        "_inventory",
        lambda *_: (
            files,
            {},
            {},
            {},
            {"state": state.to_dict(), "derived": {"unexpected/": b""}},
        ),
    )

    with pytest.raises(root.ThirdAdjudicationSubmissionError, match="inventory"):
        root._inventory(run)


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
        return root._aggregate(REQUEST, after, status="submitted", estimated=200_000, submitted=1)

    monkeypatch.setattr(root, "_run_worker", run_worker)

    result = root.process_request(REQUEST)

    assert calls == 1
    assert result["status"] == "submitted"
    assert [path.name for path, _ in writes] == [
        f"{RUN_ID}.intent.json",
        f"{RUN_ID}.json",
    ]
    assert writes[0][1]["phase72_observation_receipt_sha256"] == "4" * 64
    assert writes[0][1]["third_adjudication_part_number"] == 3


def test_submitted_checkpoint_validates_phase72_against_reconstructed_pre_state(
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
    predecessor_states: list[SpeakerReviewRunState] = []
    monkeypatch.setattr(root, "_record", lambda *_: (expected_intent, "f" * 64))
    monkeypatch.setattr(root, "_validate_binding", lambda *_: expected_intent)
    monkeypatch.setattr(
        root,
        "_pre_submission_snapshot",
        lambda files, current_state, _: (files, _state()),
    )

    def predecessor(
        _request: object, _run: object, _contents: object, state: SpeakerReviewRunState
    ) -> tuple[dict[str, str], str, str, str, int, int]:
        predecessor_states.append(state)
        preparation = {
            "config_sha": "c" * 64,
            "image": "ghcr.io/captainvc/cinegraph@sha256:" + "9" * 64,
            "release_sha": "8" * 40,
        }
        return preparation, "2" * 64, "3" * 64, "4" * 64, 200_000, 100_000

    monkeypatch.setattr(root, "_validate_phase72_predecessor", predecessor)
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
    assert predecessor_states[0].status is SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED
    assert len(predecessor_states[0].adjudication_batch_ids) == 2
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

    with pytest.raises(root.ThirdAdjudicationSubmissionError, match="binding changed"):
        root.process_request(REQUEST)


def test_worker_result_contract_and_post_inventory_are_fail_closed() -> None:
    before = _state()
    after = _state(SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED)
    valid = root._aggregate(REQUEST, after, status="submitted", estimated=200_000, submitted=1)
    with pytest.raises(root.ThirdAdjudicationSubmissionError, match="worker result"):
        root._validate_worker_result(
            {**valid, "adjudication_completed_part_count": 1},
            request=REQUEST,
            before=before,
            status="submitted",
            estimated=200_000,
            actual=100_000,
        )

    before_files = _files(before)
    after_files = _files(after)
    after_files["unexpected-private-file"] = b"tamper\n"
    with pytest.raises(root.ThirdAdjudicationSubmissionError, match="inventory"):
        root._post_validate(
            before_files,
            after_files,
            before,
            after,
            result_status="submitted",
        )


def test_phase72_predecessor_reconstructs_exact_pre_observation_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    contents = _files(state)
    contents[root._INTENT_NAME.format(part=3)] = b"pending intent\n"
    contents[root._COMPLETED_NAME.format(part=3)] = b"pending completion\n"
    intent = {key: "fixture" for key in root.phase72._BINDING_KEYS}
    intent.update(
        {
            "authorization_id": AUTHORIZATION_ID,
            "authorization_sha256": root._sha(b"phase72-auth"),
            "estimated_adjudication_cost_microusd": 200_000,
            "maximum_authorized_cost_microusd": 5_000_000,
            "pre_updated_at": "2026-01-01T00:00:00+00:00",
        }
    )
    receipt = {key: "fixture" for key in root.phase72._RECEIPT_KEYS}
    receipt["result"] = {"fixture": True}
    observed: dict[str, object] = {}
    phase_request = {
        "archive_sha256": REQUEST["archive_sha256"],
        "run_id": RUN_ID,
    }

    monkeypatch.setattr(root, "PHASE72_RECEIPTS_ROOT", tmp_path)
    monkeypatch.setattr(root, "_directory", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        root,
        "_record",
        lambda path: (intent, "1" * 64)
        if path.name.endswith(".intent.json")
        else (receipt, "2" * 64),
    )
    monkeypatch.setattr(
        root,
        "_phase72_request",
        lambda *_: (phase_request, b"phase72-auth", root._sha(b"phase72-auth")),
    )
    monkeypatch.setattr(root.phase72, "_validate_intent", lambda value, **_: value)

    def validate_submission(
        _request: object,
        _run: object,
        predecessor_contents: dict[str, bytes],
        predecessor_state: SpeakerReviewRunState,
    ) -> None:
        observed["pre_contents"] = predecessor_contents
        observed["pre_state"] = predecessor_state

    monkeypatch.setattr(root.phase72, "_validate_submission_predecessor", validate_submission)
    monkeypatch.setattr(
        root.phase72_contract,
        "validate_aggregate",
        lambda *_args, **_kwargs: {
            "run_id": RUN_ID,
            "adjudication_part_count": 3,
            "adjudication_completed_part_count": 2,
            "run_status": "adjudication_part_completed",
            "actual_primary_cost_microusd": 100_000,
            "estimated_adjudication_cost_microusd": 200_000,
        },
    )

    def validate_final(
        _receipt: object,
        *,
        intent: object,
        result: object,
        contents: dict[str, bytes],
    ) -> None:
        observed["post_contents"] = contents

    monkeypatch.setattr(root.phase72, "_validate_final_receipt", validate_final)
    monkeypatch.setattr(
        root.preparation,
        "_validate_preparation",
        lambda *_: ({"config_sha": "c", "image": "i", "release_sha": "r"}, "3" * 64),
    )

    result = root._validate_phase72_predecessor(REQUEST, tmp_path, contents, state)

    pre_state = observed["pre_state"]
    assert isinstance(pre_state, SpeakerReviewRunState)
    assert pre_state.status is SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED
    assert pre_state.adjudication_completed_part_count == 1
    assert len(pre_state.adjudication_batch_ids) == 2
    pre_contents = observed["pre_contents"]
    assert isinstance(pre_contents, dict)
    assert "adjudication-part-0002-output.jsonl" not in pre_contents
    post_contents = observed["post_contents"]
    assert isinstance(post_contents, dict)
    assert "adjudication-part-0002-output.jsonl" in post_contents
    assert root._INTENT_NAME.format(part=3) not in post_contents
    assert result[2:4] == ("1" * 64, "2" * 64)


def test_root_intent_is_pinned_to_part_three() -> None:
    state = _state()
    binding = root._binding(
        REQUEST,
        authorization_sha256="1" * 64,
        preparation_value={"config_sha": "2" * 64, "image": "image", "release_sha": "3" * 40},
        preparation_sha256="4" * 64,
        phase72_intent_sha256="5" * 64,
        phase72_receipt_sha256="6" * 64,
        estimated=200_000,
        contents=_files(state),
        state=state,
    )
    assert binding["adjudication_completed_part_count"] == 2
    assert binding["third_adjudication_part_number"] == 3
    root._validate_binding(binding, REQUEST)
    with pytest.raises(root.ThirdAdjudicationSubmissionError):
        root._validate_binding({**binding, "third_adjudication_part_number": 4}, REQUEST)
