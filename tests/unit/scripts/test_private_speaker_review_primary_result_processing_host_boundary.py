from __future__ import annotations

from pathlib import Path

from scripts import private_speaker_review_primary_result_processing_contract as contract
from scripts import private_speaker_review_primary_result_processing_host_contract as host


def test_processing_host_contract_is_a_finite_provider_free_command() -> None:
    assert host.REVIEW_PRIMARY_RESULT_PROCESSING_COMMAND == contract.COMMAND
    assert host.REVIEW_PRIMARY_RESULT_PROCESSING_HELPER_PATH.as_posix().endswith(
        "cinegraph-process-private-speaker-review-results"
    )
    assert (
        host.REVIEW_PRIMARY_RESULT_PROCESSING_RECEIPTS_ROOT.name
        == "primary-result-processing-receipts"
    )
    assert host.REVIEW_PRIMARY_RESULT_PROCESSING_HELPER_PATH.as_posix() in host.SUDOERS_CONTENT
    assert "NOPASSWD: ALL" not in host.SUDOERS_CONTENT


def test_processing_dispatch_and_helper_are_fail_closed() -> None:
    dispatcher = Path("deploy/remote/review-dispatch.sh").read_text(encoding="utf-8")
    helper = Path(
        "deploy/remote/process-private-speaker-review-results.sh"
    ).read_text(encoding="utf-8")
    assert "speaker-review-process-primary-results-v1)" in dispatcher
    assert "sudo -n /usr/local/sbin/cinegraph-process-private-speaker-review-results" in dispatcher
    assert "[[ $# -eq 0 ]]" in helper
    assert '[[ "${SUDO_USER-}" == "cinegraph-review" ]]' in helper
    assert 'python3 -I -S -B "$processor"' in helper
    assert helper.index('exec 8>"$TRANSFER_LOCK"') < helper.index('exec 9>"$DEPLOYMENT_LOCK"')
    assert helper.index('exec 9>"$DEPLOYMENT_LOCK"') < helper.index('exec 7>"$SPEAKER_REVIEW_LOCK"')
    assert "origin/main" in helper
    assert '[[ -z "${lines[16]}" ]]' in helper
    assert "|/review-workspace|false" in helper
    assert "|/review-workspace/review-runs|true" in helper
    assert "source/sha256-([0-9a-f]{64})" in helper
    assert "review-runs/sha256-${source_digest}/review-runs" in helper
    assert "OPENAI_API_KEY" not in helper
    assert "OPENAI_" in helper
    assert "eval" not in helper and "bash -c" not in helper
    assert 'docker rm --force "$CONTAINER_NAME"' in helper
    for trusted_path in (
        "scripts/run_private_speaker_review_primary_result_processing.py",
        "scripts/process_private_speaker_review_results_workspace.py",
        "scripts/private_speaker_review_primary_result_processing_contract.py",
        "scripts/private_speaker_review_primary_result_processing_host_contract.py",
        "deploy/compose.yaml",
    ):
        assert trusted_path in helper
