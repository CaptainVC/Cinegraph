from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_isolated_root_pins_fourth_part_and_stays_provider_free() -> None:
    project = Path(__file__).parents[3]
    code = f"""
import sys
sys.path.insert(0, {str(project)!r})
from scripts import run_private_speaker_review_fourth_adjudication_observation as root
assert root.contract.COMMAND == 'speaker-review-observe-fourth-adjudication-v1'
assert root.contract.PREDECESSOR_COMPLETED_PART_COUNT == 3
assert root.contract.OBSERVED_PART_NUMBER == 4
state = {{
    'status': 'adjudication_submitted', 'primary_part_count': 1,
        'adjudication_part_count': 4, 'adjudication_completed_part_count': 3,
}}
required, optional = root.worker._expected_inventory_names(state, observed=False)
assert 'adjudication-part-0004-requests.jsonl' in required
assert '.adjudication-part-0004-submission-intent.json' in required
assert '.adjudication-part-0005-submission-intent.json' not in required | optional
assert 'adjudication-part-0005-output.jsonl' not in required | optional
assert 'openai' not in sys.modules and 'qdrant_client' not in sys.modules
try:
    root._validate_submitted_state({{
            **state, 'adjudication_completed_part_count': 2,
            'adjudication_batch_ids': ['b1', 'b2', 'b3', 'b4'],
        'adjudication_input_file_ids': ['f1', 'f2', 'f3', 'f4'],
        'adjudication_batch_id': 'b4', 'adjudication_input_file_id': 'f4',
    }})
except root.FourthAdjudicationObservationError:
    pass
else:
    raise AssertionError('incomplete isolated state accepted')
print('isolated-fourth-observation-policy-ok')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "isolated-fourth-observation-policy-ok"
