from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_root_isolated_launch_stays_stdlib_only_and_rejects_generic_input() -> None:
    project = Path(__file__).parents[3]
    code = f"""
import sys
sys.path.insert(0, {str(project)!r})
from scripts import run_private_speaker_review_third_adjudication_observation as root
assert root.contract.COMMAND == 'speaker-review-observe-third-adjudication-v1'
assert root.contract.OPERATION == 'observe_third_adjudication'
assert root.contract.PREDECESSOR_COMPLETED_PART_COUNT == 2
assert root.contract.OBSERVED_PART_NUMBER == 3
required, optional = root.worker._expected_inventory_names({{
    'status': 'adjudication_part_completed',
    'primary_part_count': 1,
    'adjudication_part_count': 3,
    'adjudication_completed_part_count': 3,
}}, observed=True)
assert '.adjudication-part-0004-submission-intent.json' not in required | optional
assert '.adjudication-part-0004-submission-completed.json' not in required | optional
assert 'openai' not in sys.modules
assert 'qdrant_client' not in sys.modules
try:
    root._validate_submitted_state({{'status': 'adjudication_submitted', 'adjudication_part_count': 3, 'adjudication_completed_part_count': 1}})
except root.ThirdAdjudicationObservationError:
    pass
else:
    raise AssertionError('part-one checkpoint accepted')
print('isolated-policy-ok')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "isolated-policy-ok"
