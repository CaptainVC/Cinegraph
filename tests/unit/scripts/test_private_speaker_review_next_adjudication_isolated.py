from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review.workflow import SpeakerReviewRunState


def _run_isolated(module: str) -> subprocess.CompletedProcess[str]:
    project_root = Path(__file__).parents[3]
    code = f"""
import sys
sys.path.insert(0, {str(project_root)!r})
import json
from pathlib import Path
from scripts import {module} as root

state = {{
    'schema_version': 5, 'run_id': 'speaker-review-0123456789abcdef',
    'status': 'adjudication_part_completed', 'prompt_version': 'speaker-review-v1',
    'adjudication_model': 'gpt-5.6-terra',
    'primary_part_count': 1, 'primary_completed_part_count': 1,
    'adjudication_part_count': 3, 'adjudication_completed_part_count': 1,
    'adjudication_batch_id': 'batch-1', 'adjudication_input_file_id': 'file-1',
    'adjudication_batch_ids': ['batch-1'], 'adjudication_input_file_ids': ['file-1'],
}}
expected_names = (
    root.worker._expected_inventory_names
    if hasattr(root.worker, '_expected_inventory_names')
    else root.worker._expected_names
)
required, optional = expected_names(state)
assert 'run-state.json' in required
assert 'adjudication-part-0002-requests.jsonl' in required or 'adjudication-part-0002-requests.jsonl' in optional
contents = {{
    'adjudication-part-0001-requests.jsonl': b'{{"body":{{"max_output_tokens":640}}}}\\n',
    'adjudication-part-0002-requests.jsonl': b'{{"body":{{"max_output_tokens":640}}}}\\n',
    'adjudication-part-0003-requests.jsonl': b'{{"body":{{"max_output_tokens":640}}}}\\n',
}}
worker = getattr(root, 'worker', None)
parser = getattr(worker, '_parse_requests', None) or root.submit.worker._parse_requests
parsed = parser(contents, type('State', (), state)())
assert len(parsed) == 3
coordinator = root.submit if hasattr(root, 'submit') else root
estimate = coordinator._estimate_cost(contents, type('State', (), state)())
assert estimate == 11545, estimate
assert 'openai' not in sys.modules
assert 'qdrant_client' not in sys.modules
print('isolated-policy-ok')
"""
    return subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code],
        cwd=Path(__file__).parents[3],
        capture_output=True,
        text=True,
        check=False,
    )


def test_submission_isolated_inventory_policy_and_total_cost() -> None:
    completed = _run_isolated("run_private_speaker_review_next_adjudication")
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "isolated-policy-ok"


def test_observation_isolated_inventory_policy_and_total_cost() -> None:
    completed = _run_isolated(
        "run_private_speaker_review_next_adjudication_observation"
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "isolated-policy-ok"


@pytest.mark.skipif(os.name != "posix", reason="Host inventory enforces POSIX ownership and modes")
def test_isolated_submission_reads_real_inventory_and_reaches_worker(tmp_path: Path) -> None:
    """Exercise coordinator wiring after the independently tested trust boundary.

    Authorization, earlier receipt validation and Docker are injected. Actual
    run inventory, journal validation, cost, binding and post-state checks run
    under the same isolated interpreter flags as the VPS helper.
    """
    state = SpeakerReviewRunState(
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
        adjudication_part_count=3,
        adjudication_completed_part_count=1,
        adjudication_batch_id="batch-1",
        adjudication_input_file_id="file-1",
        adjudication_batch_ids=("batch-1",),
        adjudication_input_file_ids=("file-1",),
    )
    import json

    project = Path(__file__).parents[3]
    code = f'''
import sys, json, os
from pathlib import Path
sys.path.insert(0, {str(project)!r})
from scripts import run_private_speaker_review_next_adjudication as root
payload = json.loads({json.dumps(state.to_dict())!r})
state = root.SpeakerReviewRunState(payload)
run = Path({str(tmp_path)!r}) / state.run_id
run.mkdir(mode=0o700)
required, _ = root._expected_inventory_names(state)
contents = {{name: b"{{}}\\n" for name in required}}
for part in range(1, 4):
    contents[f"adjudication-part-{{part:04d}}-requests.jsonl"] = b'{{"body":{{"max_output_tokens":640}}}}\\n'
request_bytes = contents["adjudication-part-0001-requests.jsonl"]
binding = {{"schema_version": 1, "request_sha256": root._sha(request_bytes),
    "run_id": state.run_id, "stage": "adjudication", "part": 1,
    "prompt_version": state.prompt_version, "batch_endpoint": "/v1/responses",
    "completion_window": "24h"}}
contents[".adjudication-part-0001-submission-intent.json"] = root._canonical({{"binding": binding, "status": "intent"}})
contents[".adjudication-part-0001-submission-completed.json"] = root._canonical({{"binding": binding, "status": "completed", "batch_id": "batch-1", "input_file_id": "file-1"}})
contents[root.STATE_NAME] = root._canonical(payload)
for name, raw in contents.items():
    path = run / name
    path.write_bytes(raw)
    path.chmod(0o600)
if os.name == "posix":
    root.phase69.WORKER_UID = os.getuid()
    root.phase69.WORKER_GID = os.getgid()
root.RECEIPTS_ROOT = Path({str(tmp_path)!r}) / "receipts"
root.RECEIPTS_ROOT.mkdir()
root._validate_authorization = lambda _: "a" * 64
root._run_directory = lambda _: run
prep = {{"config_sha": "c" * 64, "image": "ghcr.io/captainvc/cinegraph@sha256:" + "d" * 64, "release_sha": "e" * 40}}
root._validate_phase70_predecessor = lambda *args: (prep, "b" * 64, "c" * 64, "d" * 64, 11545, 100000)
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
print("isolated-process-ok")
'''
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "isolated-process-ok"
