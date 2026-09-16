from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review.workflow import SpeakerReviewRunState


def _state() -> SpeakerReviewRunState:
    return SpeakerReviewRunState(
        schema_version=5,
        run_id="speaker-review-0123456789abcdef",
        status=SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED,
        created_at="2026-09-15T00:00:00+00:00",
        updated_at="2026-09-15T00:00:00+00:00",
        candidate_count=1,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        prompt_version="speaker-review-v1",
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=0.1,
        actual_primary_cost_usd=0.1,
        actual_adjudication_cost_usd=0.0,
        primary_part_count=1,
        primary_completed_part_count=1,
        adjudication_part_count=4,
        adjudication_completed_part_count=3,
        adjudication_batch_id="batch-3",
        adjudication_input_file_id="file-3",
        adjudication_batch_ids=("batch-1", "batch-2", "batch-3"),
        adjudication_input_file_ids=("file-1", "file-2", "file-3"),
    )


def test_fourth_submission_isolated_inventory_policy_and_total_cost() -> None:
    project = Path(__file__).parents[3]
    code = f"""
import sys, json
sys.path.insert(0, {str(project)!r})
from scripts import run_private_speaker_review_fourth_adjudication as root
state = {{
    'schema_version': 5, 'run_id': 'speaker-review-0123456789abcdef',
    'status': 'adjudication_part_completed', 'prompt_version': 'speaker-review-v1',
    'adjudication_model': 'gpt-5.6-terra', 'primary_part_count': 1,
    'adjudication_part_count': 4, 'adjudication_completed_part_count': 3,
    'adjudication_batch_id': 'batch-3', 'adjudication_input_file_id': 'file-3',
    'adjudication_batch_ids': ['batch-1', 'batch-2', 'batch-3'],
    'adjudication_input_file_ids': ['file-1', 'file-2', 'file-3'],
}}
view = root.SpeakerReviewRunState(state)
required, _ = root._expected_inventory_names(view)
assert 'adjudication-part-0003-requests.jsonl' in required
contents = {{
    f'adjudication-part-{{part:04d}}-requests.jsonl':
        b'{{"body":{{"max_output_tokens":640}}}}\\n'
    for part in range(1, 5)
}}
assert root._estimate_cost(contents, view) > 0
assert 'openai' not in sys.modules and 'qdrant_client' not in sys.modules
print('isolated-fourth-policy-ok')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "isolated-fourth-policy-ok"


@pytest.mark.skipif(os.name != "posix", reason="Host inventory enforces POSIX ownership and modes")
def test_isolated_fourth_submission_reads_real_inventory_and_reaches_worker(
    tmp_path: Path,
) -> None:
    """Run real root inventory, journal, cost, binding, and post checks.

    The prior authorization/receipt boundary and Docker invocation are injected;
    those boundaries have their own focused tests.
    """

    state = _state()
    project = Path(__file__).parents[3]
    code = f'''
import sys, json, os
from pathlib import Path
sys.path.insert(0, {str(project)!r})
from scripts import run_private_speaker_review_fourth_adjudication as root
payload = json.loads({json.dumps(state.to_dict())!r})
state = root.SpeakerReviewRunState(payload)
run = Path({str(tmp_path)!r}) / state.run_id
run.mkdir(mode=0o700)
required, _ = root._expected_inventory_names(state)
contents = {{name: b"{{}}\\n" for name in required}}
for part in range(1, 5):
    contents[f"adjudication-part-{{part:04d}}-requests.jsonl"] = b'{{"body":{{"max_output_tokens":640}}}}\\n'
for part, batch, input_file in ((1, "batch-1", "file-1"), (2, "batch-2", "file-2"), (3, "batch-3", "file-3")):
    request_bytes = contents[f"adjudication-part-{{part:04d}}-requests.jsonl"]
    binding = {{"schema_version": 1, "request_sha256": root._sha(request_bytes),
        "run_id": state.run_id, "stage": "adjudication", "part": part,
        "prompt_version": state.prompt_version, "batch_endpoint": root.BATCH_ENDPOINT,
        "completion_window": root.BATCH_COMPLETION_WINDOW}}
    contents[f".adjudication-part-{{part:04d}}-submission-intent.json"] = root._canonical({{"binding": binding, "status": "intent"}})
    contents[f".adjudication-part-{{part:04d}}-submission-completed.json"] = root._canonical({{"binding": binding, "status": "completed", "batch_id": batch, "input_file_id": input_file}})
contents[root.STATE_NAME] = root._canonical(payload)
for name, raw in contents.items():
    path = run / name
    path.write_bytes(raw)
    path.chmod(0o600)
root.phase69.WORKER_UID = os.getuid()
root.phase69.WORKER_GID = os.getgid()
root.RECEIPTS_ROOT = Path({str(tmp_path)!r}) / "receipts"
root.RECEIPTS_ROOT.mkdir()
root._validate_authorization = lambda _: "a" * 64
root._run_directory = lambda _: run
prep = {{"config_sha": "c" * 64, "image": "ghcr.io/captainvc/cinegraph@sha256:" + "d" * 64, "release_sha": "e" * 40}}
root._validate_phase74_predecessor = lambda *args: (prep, "b" * 64, "c" * 64, "d" * 64, 11545, 100000)
writes = []
root._write_once = lambda path, value: writes.append(value)
calls = []
def worker(request, parent, bindings):
    calls.append(bindings)
    return root._aggregate(request, state, status="reconciliation_required", estimated=11545, submitted=0)
root._run_worker = worker
request = {{"archive_sha256": "a" * 64,
    "authorization_id": "123e4567-e89b-42d3-a456-426614174000",
    "maximum_authorized_cost_microusd": 5000000,
    "operation": root.contract.OPERATION, "purpose": root.contract.PURPOSE,
    "run_id": state.run_id, "schema_version": root.contract.PROTOCOL_VERSION,
    "season_number": root.contract.SEASON_NUMBER}}
result = root.process_request(request)
assert result["status"] == "reconciliation_required"
assert len(calls) == 1 and len(writes) == 1
assert "openai" not in sys.modules and "qdrant_client" not in sys.modules
print("isolated-fourth-process-ok")
'''
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "isolated-fourth-process-ok"
