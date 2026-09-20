from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts import private_speaker_review_adjudication_result_processing_contract as contract
from scripts import run_private_speaker_review_adjudication_result_processing as processing

RUN_ID = "speaker-review-0123456789abcdef"
AUTHORIZATION_ID = "123e4567-e89b-42d3-a456-426614174000"
DIGEST = "a" * 64


def _request() -> dict[str, object]:
    return contract.validate_request(
        {
            "archive_sha256": DIGEST,
            "authorization_id": AUTHORIZATION_ID,
            "maximum_authorized_cost_microusd": 5_000_000,
            "operation": contract.OPERATION,
            "purpose": contract.PURPOSE,
            "run_id": RUN_ID,
            "schema_version": contract.PROTOCOL_VERSION,
            "season_number": contract.SEASON_NUMBER,
        }
    )


def _aggregate(status: str = "final_review_prepared") -> dict[str, object]:
    run_status = status if status != "already_processed" else "final_review_prepared"
    return {
        "accepted_by_consensus": 1,
        "accepted_by_adjudication": 0,
        "actual_adjudication_cost_microusd": 2,
        "actual_primary_cost_microusd": 3,
        "adjudication_completed_part_count": 1,
        "adjudication_part_count": 1,
        "candidate_count": 2,
        "final_review_part_count": 1 if run_status == "final_review_prepared" else 0,
        "maximum_authorized_cost_microusd": 5_000_000,
        "needs_human": 1 if run_status == "final_review_prepared" else 0,
        "operation": contract.OPERATION,
        "primary_completed_part_count": 1,
        "primary_part_count": 1,
        "purpose": contract.PURPOSE,
        "run_status": run_status,
        "season_number": contract.SEASON_NUMBER,
        "status": status,
    }


def _terminal_state(*, maximum_cost_usd: float = 5.0) -> dict[str, object]:
    return {
        "accepted_by_consensus": 1,
        "accepted_by_adjudication": 0,
        "actual_adjudication_cost_usd": 0.000002,
        "actual_final_review_cost_usd": 0.0,
        "actual_primary_cost_usd": 0.000003,
        "adjudication_completed_part_count": 1,
        "adjudication_part_count": 1,
        "candidate_count": 2,
        "final_review_batch_id": None,
        "final_review_batch_ids": [],
        "final_review_completed_part_count": 0,
        "final_review_input_file_id": None,
        "final_review_input_file_ids": [],
        "final_review_part_count": 1,
        "maximum_cost_usd": maximum_cost_usd,
        "needs_human": 1,
        "primary_completed_part_count": 1,
        "primary_part_count": 1,
        "run_id": RUN_ID,
        "status": "final_review_prepared",
    }


def test_root_entrypoint_rejects_non_root_and_never_leaks_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stderr = io.StringIO()
    monkeypatch.setattr(
        processing,
        "_require_root",
        lambda: (_ for _ in ()).throw(OSError("/private/provider detail")),
    )
    monkeypatch.setattr(processing.sys, "stderr", stderr)
    assert processing.main() == 2
    assert stderr.getvalue() == ("error=speaker_review_adjudication_result_processing_rejected\n")


def test_root_entrypoint_is_stdlib_only_under_isolated_no_site_mode() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "scripts/run_private_speaker_review_adjudication_result_processing.py",
        ],
        input=b"",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 2
    assert completed.stdout == b""
    assert completed.stderr.replace(b"\r\n", b"\n") == (
        b"error=speaker_review_adjudication_result_processing_rejected\n"
    )
    assert b"ModuleNotFoundError" not in completed.stderr


def test_isolated_root_import_keeps_application_dependencies_outside_host() -> None:
    project = Path(__file__).parents[3]
    code = f"""
import sys
sys.path.insert(0, {str(project)!r})
from scripts import run_private_speaker_review_adjudication_result_processing as root
assert root.worker._inventory is root._isolated_inventory
assert root.worker._validate_inventory_shape is root._isolated_validate_inventory_shape
assert root._IsolatedStatus('final_review_prepared').value == 'final_review_prepared'
assert 'langgraph' not in sys.modules
assert 'openai' not in sys.modules
assert 'qdrant_client' not in sys.modules
print('isolated-adjudication-processing-policy-ok')
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code],
        cwd=project,
        capture_output=True,
        text=True,
        shell=False,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "isolated-adjudication-processing-policy-ok"


@pytest.mark.parametrize("checkpoint", ["pre", "final_review_prepared", "completed"])
def test_isolated_fallback_matches_worker_inventory_for_every_checkpoint(
    checkpoint: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import process_private_speaker_review_adjudication_results_workspace as workspace
    from tests.unit.ingestion.speaker_review.test_process_adjudication_results import (
        _fixture,
        _workflow,
    )

    if checkpoint == "completed":
        run, state = _fixture(
            tmp_path,
            adjudication_action="accept_candidate",
            adjudication_confidence=0.99,
        )
        _workflow().process_adjudication_results(run, state)
    else:
        run, state = _fixture(tmp_path)
        if checkpoint == "final_review_prepared":
            _workflow().process_adjudication_results(run, state)

    if os.name == "posix":
        owner = (os.getuid(), os.getgid())
        monkeypatch.setattr(processing, "WORKER_OWNER", owner)
        monkeypatch.setattr(workspace._primary_worker, "WORKER_UID", owner[0])
        monkeypatch.setattr(workspace._primary_worker, "WORKER_GID", owner[1])
        run.chmod(0o700)
        for current, directories, filenames in os.walk(run, followlinks=False):
            root = Path(current)
            for directory in directories:
                (root / directory).chmod(0o700)
            for filename in filenames:
                (root / filename).chmod(0o600)

    expected = workspace._inventory(run)
    observed = processing._isolated_inventory(run)
    assert observed == expected
    canonical, isolated_state = processing._isolated_load_validated_run_state(run, object())
    assert canonical == run
    processing._isolated_validate_inventory_shape(observed, isolated_state)


def test_worker_arguments_bind_all_inventory_digests_and_never_mount_archive(
    tmp_path: Path,
) -> None:
    snapshot = processing.RunSnapshot(
        state=b"state\n",
        artifacts={"a": b"a"},
        requests={"request": b"request"},
        journals={"journal": b"journal"},
        outputs={"output": b"output"},
        derived={"derived": b"derived"},
    )
    arguments = processing._worker_args(
        _request(), tmp_path / "source" / f"sha256-{DIGEST}", tmp_path / "runs", snapshot
    )
    joined = "\n".join(arguments)
    assert arguments[-1] == "corpus-speaker-review-process-adjudication-results"
    assert "/review-workspace:ro" in joined
    assert f"/review-workspace/review-runs/{RUN_ID}:rw" in joined
    assert "/review-workspace/review-runs:rw" not in joined
    for name in (
        contract.ENV_EXPECTED_STATE_DIGEST,
        contract.ENV_EXPECTED_ARTIFACTS_DIGEST,
        contract.ENV_EXPECTED_REQUESTS_DIGEST,
        contract.ENV_EXPECTED_JOURNALS_DIGEST,
        contract.ENV_EXPECTED_OUTPUTS_DIGEST,
        contract.ENV_EXPECTED_DERIVED_DIGEST,
    ):
        assert f"{name}=" in joined
    assert "OPENAI_API_KEY" not in joined
    assert "--privileged" not in arguments


def test_worker_output_is_bounded_and_private_stderr_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProcess:
        pid = 123

        def __init__(self) -> None:
            self.stdout = io.BytesIO(contract.canonical_json(_aggregate()))
            self.stderr = io.BytesIO(b"private provider detail\n")

        def wait(self, timeout: int | None = None) -> int:
            del timeout
            return 0

        def poll(self) -> int:
            return 0

        def kill(self) -> None:
            return None

    monkeypatch.setattr(processing.subprocess, "Popen", lambda *_, **__: FakeProcess())
    with pytest.raises(processing.AdjudicationResultProcessingError, match="worker"):
        processing._run_worker(
            _request(),
            Path("/private/source"),
            Path("/private/runs"),
            processing.RunSnapshot(b"state", {}, {}, {}, {}, {}),
        )


def test_authorization_consumption_is_single_use_and_receipt_is_minimal() -> None:
    source = Path("scripts/run_private_speaker_review_adjudication_result_processing.py")
    text = source.read_text(encoding="utf-8")
    assert "authorization" in text
    assert "claim" in text and '"status": "claimed"' in text
    assert "OPENAI_API_KEY" not in text
    assert "provider" not in text.lower() or "provider-free" in text.lower()
    assert "archive_sha256" in text
    assert "run_id" in text
    assert "authorization_id" in text
    assert "receipt" in text


def test_atomic_publication_rejects_and_preserves_ambiguous_pending_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path.chmod(0o700)
    owner = tmp_path.stat()
    monkeypatch.setattr(processing, "ROOT_UID", owner.st_uid)
    monkeypatch.setattr(processing, "ROOT_GID", owner.st_gid)
    record = tmp_path / "record.json"
    pending = tmp_path / ".record.json.pending"
    pending.write_bytes(b'{"attacker":true}\n')
    pending.chmod(0o600)

    with pytest.raises(processing.AdjudicationResultProcessingError, match="conflict"):
        processing._write_once(record, {"safe": True})

    assert not record.exists()
    assert pending.read_bytes() == b'{"attacker":true}\n'


def test_root_aggregate_is_independently_derived_and_budget_bounded() -> None:
    assert (
        processing._aggregate(_request(), _terminal_state(), status="final_review_prepared")
        == _aggregate()
    )

    state = _terminal_state(maximum_cost_usd=0.000004)
    with pytest.raises(processing.AdjudicationResultProcessingError, match="aggregate"):
        processing._aggregate(_request(), state, status="final_review_prepared")


def test_transition_preserves_every_existing_evidence_byte() -> None:
    before = processing.RunSnapshot(
        b"before",
        {"artifact": b"a"},
        {"request": b"r"},
        {"journal": b"j"},
        {"output": b"o"},
        {"derived": b"d"},
    )
    valid_after = processing.RunSnapshot(
        b"after",
        before.artifacts,
        {**before.requests, "final-review-part-0001-requests.jsonl": b"new"},
        before.journals,
        before.outputs,
        {**before.derived, "adjudication-verdicts.jsonl": b"new"},
    )
    processing._validate_transition(before, valid_after)

    changed = processing.RunSnapshot(
        b"after",
        before.artifacts,
        {"request": b"changed"},
        before.journals,
        before.outputs,
        before.derived,
    )
    with pytest.raises(processing.AdjudicationResultProcessingError, match="changed"):
        processing._validate_transition(before, changed)


def test_final_receipt_keeps_only_non_sensitive_binding_and_post_digests() -> None:
    snapshot = processing.RunSnapshot(b"state", {}, {}, {}, {}, {})
    receipt = processing._receipt_payload(_request(), "b" * 64, "c" * 64, snapshot, _aggregate())

    assert "request" not in receipt
    assert "pre_hashes" not in receipt
    assert receipt["authorization_claim_sha256"] == "b" * 64
    assert set(receipt["post_digests"]) == {
        "state",
        "artifacts",
        "requests",
        "journals",
        "outputs",
        "derived",
    }


@pytest.mark.parametrize("raw", [contract.canonical_json(_request()) + b"x", b"{}\n"])
def test_root_request_reader_rejects_trailing_or_noncanonical_bytes(raw: bytes) -> None:
    with pytest.raises(processing.AdjudicationResultProcessingError, match="request"):
        processing._read_request(io.BytesIO(raw))
