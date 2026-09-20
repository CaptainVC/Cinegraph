from pathlib import Path

from scripts import (
    private_speaker_review_adjudication_result_processing_contract as contract,
)
from scripts import (
    private_speaker_review_adjudication_result_processing_host_contract as host,
)
from scripts import (
    private_speaker_review_fourth_adjudication_observation_host_contract as predecessor,
)


def test_host_contract_is_chained_finite_and_provider_free() -> None:
    assert host.REVIEW_ADJUDICATION_RESULT_PROCESSING_COMMAND == contract.COMMAND
    assert host.REVIEW_ADJUDICATION_RESULT_PROCESSING_HELPER_PATH == Path(
        "/usr/local/sbin/cinegraph-process-private-speaker-review-adjudication-results"
    )
    assert host.REVIEW_ADJUDICATION_RESULT_PROCESSING_RECEIPTS_ROOT.name == (
        "adjudication-result-processing-receipts"
    )
    assert host.SUDOERS_CONTENT.startswith(predecessor.SUDOERS_CONTENT)
    assert (
        host.SUDOERS_CONTENT.count("NOPASSWD:")
        == predecessor.SUDOERS_CONTENT.count("NOPASSWD:") + 1
    )
    assert "NOPASSWD: ALL" not in host.SUDOERS_CONTENT
    assert "OPENAI_API_KEY" not in host.SUDOERS_CONTENT


def test_offline_compose_service_is_digest_selected_secretless_and_bounded() -> None:
    compose = Path("deploy/compose.yaml").read_text(encoding="utf-8")
    service = compose.split("\n  corpus-speaker-review-process-adjudication-results:", 1)[1].split(
        "\n  postgres:", 1
    )[0]
    assert "profiles: [corpus-speaker-review-process-adjudication-results]" in service
    assert "network_mode: none" in service
    assert 'user: "10002:10002"' in service
    assert (
        'command: ["python", "scripts/process_private_speaker_review_adjudication_results_workspace.py"]'
        in service
    )
    assert "read_only: true" in service
    assert "cap_drop:" in service and "- ALL" in service
    assert "no-new-privileges:true" in service
    assert "OPENAI_API_KEY" not in service
    assert host.REVIEW_ADJUDICATION_RESULT_PROCESSING_SOURCE_MOUNT.as_posix() == (
        "/review-workspace"
    )
    assert host.REVIEW_ADJUDICATION_RESULT_PROCESSING_RUNS_MOUNT.as_posix() == (
        "/review-workspace/review-runs"
    )


def test_forced_dispatcher_and_root_helper_accept_only_exact_processing_command() -> None:
    dispatcher = Path("deploy/remote/review-dispatch.sh").read_text(encoding="utf-8")
    helper = Path("deploy/remote/process-private-speaker-review-adjudication-results.sh").read_text(
        encoding="utf-8"
    )

    assert "speaker-review-process-adjudication-results-v1)" in dispatcher
    assert (
        "sudo -n /usr/local/sbin/cinegraph-process-private-speaker-review-adjudication-results"
        in dispatcher
    )
    assert "[[ $# -eq 0 ]]" in helper
    assert '[[ "${SUDO_USER-}" == "cinegraph-review" ]]' in helper
    assert 'python3 -I -S -B "$processor"' in helper
    assert "eval" not in helper and "bash -c" not in helper
    assert "OPENAI_API_KEY" not in helper
    assert "OPENAI_" in helper
    assert "docker rm --force" in helper
    assert helper.index('exec 8>"$TRANSFER_LOCK"') < helper.index('exec 9>"$DEPLOYMENT_LOCK"')
    assert helper.index('exec 9>"$DEPLOYMENT_LOCK"') < helper.index('exec 7>"$SPEAKER_REVIEW_LOCK"')
    assert "review-runs/(speaker-review-[0-9a-f]{16})" in helper
    assert "review-runs/${run_id}|true" in helper
    assert '[[ "$run_digest" == "${BASH_REMATCH[1]}" ]]' in helper
    assert "docker inspect --format" in helper
    assert "ReadonlyRootfs" in helper and "Privileged" in helper
    assert ".HostConfig.Memory" in helper and ".HostConfig.NanoCpus" in helper
    assert 'git -C "$release_dir" ls-files -z' in helper
    assert 'find "$release_dir" -xdev -type d -print0' in helper
    for trusted_path in (
        "scripts/run_private_speaker_review_adjudication_result_processing.py",
        "scripts/process_private_speaker_review_adjudication_results_workspace.py",
        "scripts/private_speaker_review_adjudication_result_processing_contract.py",
        "scripts/private_speaker_review_adjudication_result_processing_host_contract.py",
        "deploy/compose.yaml",
    ):
        assert trusted_path in helper


def test_bootstrap_allowlist_contains_new_files_and_forced_entry() -> None:
    bootstrap = Path("scripts/bootstrap_review_host.py").read_text(encoding="utf-8")
    for value in (
        "REVIEW_ADJUDICATION_RESULT_PROCESSING_HELPER_PATH",
        "SOURCE_ADJUDICATION_RESULT_PROCESSING_HELPER",
        "SOURCE_DISPATCH",
    ):
        assert value in bootstrap
