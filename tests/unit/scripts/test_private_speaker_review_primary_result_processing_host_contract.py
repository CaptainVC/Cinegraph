from pathlib import Path

from scripts import private_speaker_review_next_primary_observation_host_contract as previous
from scripts import private_speaker_review_primary_result_processing_contract as contract
from scripts import private_speaker_review_primary_result_processing_host_contract as host


def test_processing_host_contract_is_chained_and_finite() -> None:
    assert host.REVIEW_PRIMARY_RESULT_PROCESSING_COMMAND == contract.COMMAND
    assert host.REVIEW_PRIMARY_RESULT_PROCESSING_HELPER_PATH == Path(
        "/usr/local/sbin/cinegraph-process-private-speaker-review-results"
    )
    assert host.REVIEW_PRIMARY_RESULT_PROCESSING_RECEIPTS_ROOT == (
        host.SPEAKER_REVIEW_ROOT / "primary-result-processing-receipts"
    )
    assert host.SUDOERS_CONTENT.startswith(previous.SUDOERS_CONTENT)
    assert host.SUDOERS_CONTENT.count("NOPASSWD:") == 5
    assert "NOPASSWD: ALL" not in host.SUDOERS_CONTENT


def test_processing_host_contract_matches_existing_offline_service() -> None:
    compose = Path("deploy/compose.yaml").read_text(encoding="utf-8")
    service = compose.split(
        "\n  corpus-speaker-review-process-primary-results:", 1
    )[1].split("\n  postgres:", 1)[0]
    assert (
        f"profiles: [{host.REVIEW_PRIMARY_RESULT_PROCESSING_COMPOSE_PROFILE}]"
        in service
    )
    assert "network_mode: none" in service
    assert 'user: "10002:10002"' in service
    assert (
        'command: ["python", "scripts/process_private_speaker_review_results_workspace.py"]'
        in service
    )
    assert host.SPEAKER_REVIEW_SOURCE_ROOT.name == "source"
    assert host.SPEAKER_REVIEW_RUNS_ROOT.name == "review-runs"
    assert host.REVIEW_PRIMARY_RESULT_PROCESSING_RUNS_MOUNT.as_posix() == (
        "/review-workspace/review-runs"
    )
    assert host.REVIEW_PRIMARY_RESULT_PROCESSING_SOURCE_MOUNT.as_posix() == (
        "/review-workspace"
    )
