from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest
from scripts import private_speaker_review_primary_result_processing_contract as contract
from scripts import run_private_speaker_review_primary_result_processing as processing

RUN_ID = "speaker-review-0123456789abcdef"
AUTHORIZATION_ID = "12345678-1234-4123-8123-123456789abc"
DIGEST = "a" * 64


def request() -> dict[str, object]:
    return contract.validate_request(
        {
            "archive_sha256": DIGEST,
            "authorization_id": AUTHORIZATION_ID,
            "maximum_authorized_cost_microusd": 500_000,
            "operation": contract.OPERATION,
            "purpose": contract.PURPOSE,
            "run_id": RUN_ID,
            "schema_version": contract.PROTOCOL_VERSION,
            "season_number": contract.SEASON_NUMBER,
        }
    )


def state(status: str) -> dict[str, object]:
    return {
        "accepted_by_consensus": 3 if status == "completed" else 2,
        "adjudication_part_count": 0 if status == "completed" else 1,
        "candidate_count": 3,
        "needs_human": 0,
        "primary_completed_part_count": 2,
        "primary_part_count": 2,
        "run_id": RUN_ID,
        "status": status,
        "updated_at": "2026-09-11T00:00:00Z",
        # These fields prove that processing-state binding ignores only the
        # explicitly mutable transition fields.
        "model": "gpt-5.6-luna",
        "actual_primary_cost_usd": 0.1 if status != "primary_part_completed" else 0.0,
        "actual_total_cost_usd": 0.1 if status != "primary_part_completed" else 0.0,
    }


def snapshot(*, status: str, derived: dict[str, bytes] | None = None) -> processing.RunSnapshot:
    run_state = processing._canonical(state(status))
    return processing.RunSnapshot(
        state=run_state,
        artifacts={
            "candidates.jsonl": b"{}\n",
            "source-manifest.json": b'{"schema_version":2,"sources":{}}\n',
            "primary-part-0001-requests.jsonl": b"{}\n",
            "primary-part-0002-requests.jsonl": b"{}\n",
        },
        journals={
            ".primary-part-0001-submission-intent.json": b"{}\n",
            ".primary-part-0001-submission-completed.json": b"{}\n",
            ".primary-part-0002-submission-intent.json": b"{}\n",
            ".primary-part-0002-submission-completed.json": b"{}\n",
        },
        outputs={
            "primary-part-0001-output.jsonl": b"{}\n",
            "primary-part-0002-output.jsonl": b"{}\n",
        },
        derived={} if derived is None else derived,
    )


def prep() -> dict[str, object]:
    return {
        "config_sha": "b" * 64,
        "estimated": 400_000,
        "image": "ghcr.io/captainvc/cinegraph@sha256:" + "c" * 64,
        "receipt": {"artifact_set_sha256": "d" * 64},
        "receipt_sha": "e" * 64,
        "release_sha": "f" * 40,
        "result": {"candidate_count": 3, "primary_part_count": 2},
    }


def terminal_worker_response() -> dict[str, object]:
    return {
        "accepted_by_consensus": 3,
        "adjudication_part_count": 0,
        "candidate_count": 3,
        "needs_human": 0,
        "operation": contract.OPERATION,
        "primary_completed_part_count": 2,
        "primary_part_count": 2,
        "purpose": contract.PURPOSE,
        "run_id": RUN_ID,
        "run_status": "completed",
        "season_number": contract.SEASON_NUMBER,
        "status": "completed",
    }


def _patch_common(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    snapshots: list[processing.RunSnapshot],
    states: list[dict[str, object]],
) -> list[tuple[Path, dict[str, object]]]:
    writes: list[tuple[Path, dict[str, object]]] = []
    monkeypatch.setattr(processing, "RECEIPTS_ROOT", tmp_path)
    monkeypatch.setattr(processing, "_directory", lambda *args, **kwargs: None)
    monkeypatch.setattr(processing, "_validate_authorization", lambda _: "1" * 64)
    monkeypatch.setattr(
        processing.next_submission, "_validate_preparation", lambda _: (prep(), "e" * 64)
    )
    monkeypatch.setattr(processing, "_source_workspace", lambda _: tmp_path / "source")
    monkeypatch.setattr(
        processing,
        "_run_directory",
        lambda _: (tmp_path / "review-runs", tmp_path / RUN_ID),
    )
    snapshot_iterator = iter(snapshots)
    state_iterator = iter(states)
    monkeypatch.setattr(processing, "_read_inventory", lambda _: next(snapshot_iterator))
    monkeypatch.setattr(processing, "_state", lambda _: next(state_iterator))
    monkeypatch.setattr(processing, "_validate_inventory_shape", lambda *_: None)
    monkeypatch.setattr(processing, "_validate_pre_state", lambda *_: None)
    monkeypatch.setattr(processing, "_phase66_evidence", lambda *_: ("2" * 64, "3" * 64))
    monkeypatch.setattr(
        processing,
        "_write_once",
        lambda path, value: writes.append((path, dict(value))),
    )
    monkeypatch.setattr(
        processing.next_submission,
        "_active_binding",
        lambda: (prep()["release_sha"], prep()["image"], prep()["config_sha"]),
    )
    return writes


def test_process_request_publishes_intent_then_receipt_for_offline_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before_state = state("primary_part_completed")
    after_state = state("adjudication_prepared")
    before = snapshot(status="primary_part_completed")
    after = snapshot(
        status="adjudication_prepared",
        derived={
            "primary-verdicts.jsonl": b"{}\n",
            "primary-parse-errors.json": b"{}\n",
            "primary-decisions.jsonl": b"{}\n",
            "adjudication-part-0001-requests.jsonl": b"{}\n",
        },
    )
    writes = _patch_common(monkeypatch, tmp_path, [before, after], [before_state, after_state])
    expected = processing._aggregate(request(), after_state, "adjudication_prepared")
    monkeypatch.setattr(processing, "_run_worker", lambda *_: expected)

    assert processing.process_request(request()) == expected
    assert [path.name for path, _ in writes] == [
        f"{RUN_ID}.intent.json",
        f"{RUN_ID}.json",
    ]
    intent = writes[0][1]
    assert intent["status"] == "intent"
    assert set(intent) == processing._BINDING_KEYS
    assert intent["pre_derived_hashes"] == {}
    assert all("batch" not in key and "file_id" not in key for key in intent)
    receipt = writes[1][1]
    assert receipt["result"] == expected
    assert receipt["status"] == "adjudication_prepared"


def test_process_request_rejects_unattributed_derived_files_before_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_state = state("primary_part_completed")
    current = snapshot(
        status="primary_part_completed",
        derived={"primary-verdicts.jsonl": b"partial\n"},
    )
    writes = _patch_common(monkeypatch, tmp_path, [current], [current_state])

    with pytest.raises(processing.PrimaryResultProcessingError, match="intent missing"):
        processing.process_request(request())
    assert writes == []


def test_process_request_rejects_predecessor_failure_without_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_state = state("primary_part_completed")
    current = snapshot(status="primary_part_completed")
    writes = _patch_common(monkeypatch, tmp_path, [current], [current_state])
    monkeypatch.setattr(
        processing,
        "_phase66_evidence",
        lambda *_: (_ for _ in ()).throw(
            processing.PrimaryResultProcessingError("final observation evidence invalid")
        ),
    )
    invoked = False

    def worker(*_: object) -> dict[str, object]:
        nonlocal invoked
        invoked = True
        return {}

    monkeypatch.setattr(processing, "_run_worker", worker)
    with pytest.raises(processing.PrimaryResultProcessingError, match="final observation"):
        processing.process_request(request())
    assert writes == []
    assert not invoked


def test_process_request_rejects_invalid_worker_result_without_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before_state = state("primary_part_completed")
    after_state = state("adjudication_prepared")
    before = snapshot(status="primary_part_completed")
    after = snapshot(
        status="adjudication_prepared",
        derived={
            "primary-verdicts.jsonl": b"{}\n",
            "primary-parse-errors.json": b"{}\n",
            "primary-decisions.jsonl": b"{}\n",
            "adjudication-part-0001-requests.jsonl": b"{}\n",
        },
    )
    writes = _patch_common(monkeypatch, tmp_path, [before, after], [before_state, after_state])
    invalid = processing._aggregate(request(), after_state, "adjudication_prepared")
    invalid["accepted_by_consensus"] = 0
    monkeypatch.setattr(processing, "_run_worker", lambda *_: invalid)

    with pytest.raises(processing.PrimaryResultProcessingError, match="worker result"):
        processing.process_request(request())
    assert [path.name for path, _ in writes] == [f"{RUN_ID}.intent.json"]


def test_process_request_rejects_active_runtime_drift_without_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before_state = state("primary_part_completed")
    after_state = state("adjudication_prepared")
    before = snapshot(status="primary_part_completed")
    after = snapshot(
        status="adjudication_prepared",
        derived={
            "primary-verdicts.jsonl": b"{}\n",
            "primary-parse-errors.json": b"{}\n",
            "primary-decisions.jsonl": b"{}\n",
            "adjudication-part-0001-requests.jsonl": b"{}\n",
        },
    )
    writes = _patch_common(monkeypatch, tmp_path, [before, after], [before_state, after_state])
    expected = processing._aggregate(request(), after_state, "adjudication_prepared")
    monkeypatch.setattr(processing, "_run_worker", lambda *_: expected)
    monkeypatch.setattr(
        processing.next_submission,
        "_active_binding",
        lambda: ("0" * 40, prep()["image"], prep()["config_sha"]),
    )

    with pytest.raises(processing.PrimaryResultProcessingError, match="runtime changed"):
        processing.process_request(request())
    assert [path.name for path, _ in writes] == [f"{RUN_ID}.intent.json"]


def test_terminal_receipt_replay_never_runs_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = snapshot(status="primary_part_completed")
    before_state = state("primary_part_completed")
    after_state = state("adjudication_prepared")
    after = snapshot(
        status="adjudication_prepared",
        derived={
            "primary-verdicts.jsonl": b"{}\n",
            "primary-parse-errors.json": b"{}\n",
            "primary-decisions.jsonl": b"{}\n",
            "adjudication-part-0001-requests.jsonl": b"{}\n",
        },
    )
    writes = _patch_common(monkeypatch, tmp_path, [after], [after_state])
    intent = processing._intent_payload(
        request(), prep(), "1" * 64, "2" * 64, "3" * 64, before, before_state
    )
    terminal = processing._aggregate(request(), after_state, "adjudication_prepared")
    receipt = processing._receipt_payload(intent, terminal, after)
    intent_path = tmp_path / f"{RUN_ID}.intent.json"
    receipt_path = tmp_path / f"{RUN_ID}.json"
    intent_path.touch()
    receipt_path.touch()

    def read_record(path: Path) -> tuple[dict[str, object], str]:
        return (intent, "4" * 64) if path == intent_path else (receipt, "5" * 64)

    monkeypatch.setattr(processing, "_read_record", read_record)
    monkeypatch.setattr(processing, "_validate_predecessor_replay", lambda *_: None)
    monkeypatch.setattr(
        processing,
        "_run_worker",
        lambda *_: pytest.fail("offline worker ran during receipt replay"),
    )

    result = processing.process_request(request())

    assert result["status"] == "already_processed"
    assert result["run_status"] == "adjudication_prepared"
    assert writes == []


def test_terminal_receipt_tamper_is_rejected_without_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = snapshot(status="primary_part_completed")
    before_state = state("primary_part_completed")
    after_state = state("adjudication_prepared")
    after = snapshot(
        status="adjudication_prepared",
        derived={
            "primary-verdicts.jsonl": b"{}\n",
            "primary-parse-errors.json": b"{}\n",
            "primary-decisions.jsonl": b"{}\n",
            "adjudication-part-0001-requests.jsonl": b"{}\n",
        },
    )
    writes = _patch_common(monkeypatch, tmp_path, [after], [after_state])
    intent = processing._intent_payload(
        request(), prep(), "1" * 64, "2" * 64, "3" * 64, before, before_state
    )
    terminal = processing._aggregate(request(), after_state, "adjudication_prepared")
    receipt = processing._receipt_payload(intent, terminal, after)
    receipt["post_derived_set_sha256"] = "9" * 64
    intent_path = tmp_path / f"{RUN_ID}.intent.json"
    receipt_path = tmp_path / f"{RUN_ID}.json"
    intent_path.touch()
    receipt_path.touch()

    def read_record(path: Path) -> tuple[dict[str, object], str]:
        return (intent, "4" * 64) if path == intent_path else (receipt, "5" * 64)

    monkeypatch.setattr(processing, "_read_record", read_record)
    monkeypatch.setattr(processing, "_validate_predecessor_replay", lambda *_: None)
    monkeypatch.setattr(
        processing,
        "_run_worker",
        lambda *_: pytest.fail("offline worker ran before receipt rejection"),
    )

    with pytest.raises(processing.PrimaryResultProcessingError, match="receipt evidence"):
        processing.process_request(request())
    assert writes == []


def test_receipt_result_must_match_independently_computed_state() -> None:
    before = snapshot(status="primary_part_completed")
    before_state = state("primary_part_completed")
    after_state = state("adjudication_prepared")
    after = snapshot(
        status="adjudication_prepared",
        derived={
            "primary-verdicts.jsonl": b"{}\n",
            "primary-parse-errors.json": b"{}\n",
            "primary-decisions.jsonl": b"{}\n",
            "adjudication-part-0001-requests.jsonl": b"{}\n",
        },
    )
    intent = processing._intent_payload(
        request(), prep(), "1" * 64, "2" * 64, "3" * 64, before, before_state
    )
    expected = processing._aggregate(request(), after_state, "adjudication_prepared")
    receipt = processing._receipt_payload(intent, expected, after)
    mismatched = dict(expected)
    mismatched["accepted_by_consensus"] = 1
    receipt["result"] = mismatched

    with pytest.raises(processing.PrimaryResultProcessingError, match="receipt invalid"):
        processing._validate_receipt(
            receipt,
            intent=intent,
            snapshot=after,
            expected_status="adjudication_prepared",
            expected_result=expected,
        )


def test_terminal_recovery_revalidates_with_offline_worker_before_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = snapshot(status="primary_part_completed")
    before_state = state("primary_part_completed")
    after_state = state("adjudication_prepared")
    after = snapshot(
        status="adjudication_prepared",
        derived={
            "primary-verdicts.jsonl": b"{}\n",
            "primary-parse-errors.json": b"{}\n",
            "primary-decisions.jsonl": b"{}\n",
            "adjudication-part-0001-requests.jsonl": b"{}\n",
        },
    )
    writes = _patch_common(
        monkeypatch,
        tmp_path,
        [after, after],
        [after_state, after_state],
    )
    intent = processing._intent_payload(
        request(), prep(), "1" * 64, "2" * 64, "3" * 64, before, before_state
    )
    intent_path = tmp_path / f"{RUN_ID}.intent.json"
    intent_path.touch()
    monkeypatch.setattr(processing, "_read_record", lambda _: (intent, "4" * 64))
    monkeypatch.setattr(processing, "_validate_predecessor_replay", lambda *_: None)
    expected_replay = processing._aggregate(request(), after_state, "already_processed")
    calls: list[processing.RunSnapshot] = []

    def worker(
        _: dict[str, object],
        __: Path,
        ___: Path,
        current: processing.RunSnapshot,
    ) -> dict[str, object]:
        calls.append(current)
        return expected_replay

    monkeypatch.setattr(processing, "_run_worker", worker)

    result = processing.process_request(request())

    assert result == processing._aggregate(request(), after_state, "adjudication_prepared")
    assert calls == [after]
    assert [path.name for path, _ in writes] == [f"{RUN_ID}.json"]
    assert writes[0][1]["status"] == "adjudication_prepared"


def test_terminal_recovery_rejects_worker_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = snapshot(status="primary_part_completed")
    before_state = state("primary_part_completed")
    after_state = state("adjudication_prepared")
    after = snapshot(
        status="adjudication_prepared",
        derived={
            "primary-verdicts.jsonl": b"{}\n",
            "primary-parse-errors.json": b"{}\n",
            "primary-decisions.jsonl": b"{}\n",
            "adjudication-part-0001-requests.jsonl": b"{}\n",
        },
    )
    changed = processing.RunSnapshot(
        after.state,
        after.artifacts,
        after.journals,
        after.outputs,
        {**after.derived, "primary-verdicts.jsonl": b'{"changed":true}\n'},
    )
    writes = _patch_common(
        monkeypatch,
        tmp_path,
        [after, changed],
        [after_state, after_state],
    )
    intent = processing._intent_payload(
        request(), prep(), "1" * 64, "2" * 64, "3" * 64, before, before_state
    )
    (tmp_path / f"{RUN_ID}.intent.json").touch()
    monkeypatch.setattr(processing, "_read_record", lambda _: (intent, "4" * 64))
    monkeypatch.setattr(processing, "_validate_predecessor_replay", lambda *_: None)
    monkeypatch.setattr(
        processing,
        "_run_worker",
        lambda *_: processing._aggregate(request(), after_state, "already_processed"),
    )

    with pytest.raises(processing.PrimaryResultProcessingError, match="recovery result"):
        processing.process_request(request())
    assert writes == []


def test_worker_args_bind_all_five_digests_and_exact_mounts(tmp_path: Path) -> None:
    current = snapshot(status="primary_part_completed")
    source = tmp_path / "source" / f"sha256-{DIGEST}"
    runs = tmp_path / "review-runs" / f"sha256-{DIGEST}" / "review-runs"

    arguments = processing._worker_args(request(), source, runs, current)
    joined = "\n".join(arguments)

    assert arguments[-1] == "corpus-speaker-review-process-primary-results"
    assert f"{source.as_posix()}:/review-workspace:ro" in arguments
    assert f"{runs.as_posix()}:/review-workspace/review-runs:rw" in arguments
    for name in (
        contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256,
        contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256,
        contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256,
        contract.ENV_EXPECTED_PRE_OUTPUT_SET_SHA256,
        contract.ENV_EXPECTED_PRE_DERIVED_SET_SHA256,
    ):
        assert f"{name}=" in joined
    assert "OPENAI_API_KEY" not in joined
    assert "--privileged" not in arguments


@pytest.mark.parametrize(
    ("stdout", "stderr", "returncode"),
    [
        (b"x" * (contract.OUTPUT_MAX_BYTES + 1), b"", 0),
        (contract.canonical_json(terminal_worker_response()), b"private worker detail\n", 0),
        (contract.canonical_json(terminal_worker_response()), b"", 1),
    ],
)
def test_worker_rejects_oversized_output_stderr_or_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
    stdout: bytes,
    stderr: bytes,
    returncode: int,
) -> None:
    class FakeProcess:
        pid = 123

        def __init__(self) -> None:
            self.stdout = io.BytesIO(stdout)
            self.stderr = io.BytesIO(stderr)

        def wait(self, timeout: int | None = None) -> int:
            del timeout
            return returncode

        def poll(self) -> int:
            return returncode

    monkeypatch.setattr(processing.subprocess, "Popen", lambda *_, **__: FakeProcess())

    with pytest.raises(processing.PrimaryResultProcessingError, match="worker failed"):
        processing._run_worker(
            request(),
            Path("/private/source"),
            Path("/private/review-runs"),
            snapshot(status="primary_part_completed"),
        )


def test_worker_timeout_terminates_and_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProcess:
        pid = 123

        def __init__(self) -> None:
            self.stdout = io.BytesIO(b"")
            self.stderr = io.BytesIO(b"")
            self.running = True
            self.waits = 0

        def wait(self, timeout: int | None = None) -> int:
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("worker", timeout)
            self.running = False
            return -9

        def poll(self) -> int | None:
            return None if self.running else -9

        def kill(self) -> None:
            self.running = False

    fake = FakeProcess()
    monkeypatch.setattr(processing.subprocess, "Popen", lambda *_, **__: fake)
    monkeypatch.setattr(processing.os, "name", "nt")

    with pytest.raises(processing.PrimaryResultProcessingError, match="worker failed"):
        processing._run_worker(
            request(),
            Path("/private/source"),
            Path("/private/review-runs"),
            snapshot(status="primary_part_completed"),
        )
    assert not fake.running


def test_inventory_shape_accepts_only_exact_adjudication_checkpoint() -> None:
    current_state = state("adjudication_prepared")
    valid = snapshot(
        status="adjudication_prepared",
        derived={
            "primary-verdicts.jsonl": b"{}\n",
            "primary-parse-errors.json": b"{}\n",
            "primary-decisions.jsonl": b"{}\n",
            "adjudication-part-0001-requests.jsonl": b"{}\n",
        },
    )
    processing._validate_inventory_shape(valid, current_state)

    invalid = processing.RunSnapshot(
        valid.state,
        valid.artifacts,
        valid.journals,
        valid.outputs,
        {**valid.derived, "unexpected.json": b"{}\n"},
    )
    with pytest.raises(processing.PrimaryResultProcessingError, match="inventory"):
        processing._validate_inventory_shape(invalid, current_state)


def test_intent_validation_rejects_nonempty_prederived_set() -> None:
    current = snapshot(status="primary_part_completed")
    current_state = state("primary_part_completed")
    intent = processing._intent_payload(
        request(), prep(), "1" * 64, "2" * 64, "3" * 64, current, current_state
    )
    intent["pre_derived_hashes"] = {"unexpected": "4" * 64}

    with pytest.raises(processing.PrimaryResultProcessingError, match="intent"):
        processing._validate_intent(
            intent,
            request=request(),
            prep=prep(),
            authorization_sha="1" * 64,
        )


def test_read_request_rejects_trailing_bytes() -> None:
    raw = contract.canonical_json(request()) + b"x"
    with pytest.raises(processing.PrimaryResultProcessingError, match="request"):
        processing._read_request(io.BytesIO(raw))


def test_stable_read_rejects_hardlinked_evidence(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_bytes(b"{}\n")
    if processing.os.name == "posix":
        evidence.chmod(0o600)
    processing.os.link(evidence, tmp_path / "second-link.json")

    with pytest.raises(processing.PrimaryResultProcessingError, match="unavailable"):
        processing._stable(
            evidence,
            maximum=processing.MAX_RECORD_BYTES,
            mode=0o600,
            owner=(evidence.stat().st_uid, evidence.stat().st_gid),
        )


def test_main_emits_only_generic_error(monkeypatch: pytest.MonkeyPatch) -> None:
    stderr = io.StringIO()
    monkeypatch.setattr(processing, "_require_root", lambda: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(processing.sys, "stderr", stderr)

    assert processing.main() == 2
    assert stderr.getvalue() == "error=speaker_review_primary_result_processing_rejected\n"


def test_write_once_repairs_published_pending_hardlink_after_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(processing, "RECEIPTS_ROOT", tmp_path)
    monkeypatch.setattr(processing, "ROOT_UID", tmp_path.stat().st_uid)
    monkeypatch.setattr(processing, "ROOT_GID", tmp_path.stat().st_gid)
    monkeypatch.setattr(processing, "_directory", lambda *args, **kwargs: None)
    value = {"schema_version": 1, "status": "intent"}
    published = tmp_path / "receipt.json"
    pending = tmp_path / ".receipt.json.pending"
    pending.write_bytes(processing._canonical(value))
    if processing.os.name == "posix":
        pending.chmod(0o600)
    processing.os.link(pending, published)

    processing._write_once(published, value)

    assert published.read_bytes() == processing._canonical(value)
    assert not pending.exists()
    assert published.stat().st_nlink == 1


def test_read_record_repairs_published_pending_hardlink_after_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(processing, "ROOT_UID", tmp_path.stat().st_uid)
    monkeypatch.setattr(processing, "ROOT_GID", tmp_path.stat().st_gid)
    if processing.os.name == "posix":
        tmp_path.chmod(0o700)
    value = {"schema_version": 1, "status": "intent"}
    published = tmp_path / "receipt.json"
    pending = tmp_path / ".receipt.json.pending"
    pending.write_bytes(processing._canonical(value))
    if processing.os.name == "posix":
        pending.chmod(0o600)
    processing.os.link(pending, published)

    decoded, digest = processing._read_record(published)

    assert decoded == value
    assert digest == processing._sha(processing._canonical(value))
    assert not pending.exists()
    assert published.stat().st_nlink == 1
