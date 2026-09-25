from __future__ import annotations

import sys
from pathlib import Path

import pytest
from scripts import private_speaker_review_final_review_observation_client as client
from scripts import private_speaker_review_final_review_observation_host_contract as host
from scripts.private_speaker_review_final_review_observation_contract import OUTPUT_MAX_BYTES


def test_ssh_runner_drains_oversized_stdout_and_stderr(tmp_path: Path) -> None:
    wire = tmp_path / "request.json"
    wire.write_bytes(b"{}\n")
    payload_size = OUTPUT_MAX_BYTES * 20
    code = f"import os; os.write(1, b'x' * {payload_size}); os.write(2, b'y' * {payload_size})"

    result = client._run_ssh([sys.executable, "-c", code], wire)

    assert result.returncode == 0
    assert len(result.stdout) == OUTPUT_MAX_BYTES + 1
    assert len(result.stderr) == OUTPUT_MAX_BYTES + 1


def test_ssh_runner_kills_child_on_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wire = tmp_path / "request.json"
    wire.write_bytes(b"{}\n")
    monkeypatch.setattr(host, "REVIEW_FINAL_REVIEW_OBSERVATION_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(host, "REVIEW_FINAL_REVIEW_OBSERVATION_KILL_AFTER_SECONDS", 0)
    monkeypatch.setattr(host, "CLIENT_TIMEOUT_MARGIN_SECONDS", 0)

    with pytest.raises(client.FinalReviewObservationClientError):
        client._run_ssh([sys.executable, "-c", "import time; time.sleep(10)"], wire)
