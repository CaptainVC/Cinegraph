from pathlib import Path

from scripts import bootstrap_review_host
from scripts import private_speaker_review_next_adjudication_observation_contract as contract
from scripts import private_speaker_review_next_adjudication_observation_host_contract as host
from scripts.private_speaker_review_next_adjudication_host_contract import (
    SUDOERS_CONTENT as phase71_sudoers,
)


def test_host_policy_is_one_finite_command_after_phase71() -> None:
    assert host.REVIEW_NEXT_ADJUDICATION_OBSERVATION_COMMAND == contract.COMMAND
    assert host.SUDOERS_CONTENT.startswith(phase71_sudoers)
    assert host.SUDOERS_CONTENT.count("NOPASSWD:") == phase71_sudoers.count("NOPASSWD:") + 1
    assert host.REVIEW_NEXT_ADJUDICATION_OBSERVATION_HELPER_PATH.as_posix() in host.SUDOERS_CONTENT
    assert "NOPASSWD: ALL" not in host.SUDOERS_CONTENT
    assert host.REVIEW_NEXT_ADJUDICATION_SUBMISSION_RECEIPTS_ROOT.name == "next-adjudication-submission-receipts"
    assert host.REVIEW_NEXT_ADJUDICATION_OBSERVATION_RECEIPTS_ROOT.name == "next-adjudication-observation-receipts"


def test_dispatcher_helper_and_bootstrap_are_root_only_and_fail_closed() -> None:
    dispatcher = Path("deploy/remote/review-dispatch.sh").read_text(encoding="utf-8")
    helper = Path("deploy/remote/observe-next-private-speaker-review-adjudication.sh").read_text(encoding="utf-8")
    assert f"{contract.COMMAND})" in dispatcher
    assert "sudo -n /usr/local/sbin/cinegraph-observe-next-private-speaker-review-adjudication" in dispatcher
    assert "[[ $# -eq 0 ]]" in helper
    assert '[[ "${SUDO_USER-}" == "cinegraph-review" ]]' in helper
    assert 'python3 -I -S -B "$processor"' in helper
    assert "flock -n 8" in helper and "flock -w 10 9" in helper and "flock -n 7" in helper
    assert "eval" not in helper and "bash -c" not in helper
    assert "--env OPENAI_API_KEY" not in helper and "${OPENAI_API_KEY" not in helper
    assert "origin/main" in helper
    assert "scripts/run_private_speaker_review_next_adjudication_observation.py" in helper
    assert "scripts/observe_next_private_speaker_review_adjudication_workspace.py" in helper
    assert "deploy/compose.yaml" in helper
    directories = {item.path for item in bootstrap_review_host.DIRECTORY_CONTRACT}
    files = {item.path for item in bootstrap_review_host.FILE_CONTRACT}
    assert host.REVIEW_NEXT_ADJUDICATION_OBSERVATION_RECEIPTS_ROOT in directories
    assert host.REVIEW_NEXT_ADJUDICATION_OBSERVATION_HELPER_PATH in files


def test_compose_observer_is_egress_only_unprivileged_and_secret_file_only() -> None:
    compose = Path("deploy/compose.yaml").read_text(encoding="utf-8")
    service = compose.split("\n  corpus-speaker-review-observe-next-adjudication:", 1)[1].split("\n  postgres:", 1)[0]
    assert f"profiles: [{host.REVIEW_NEXT_ADJUDICATION_OBSERVATION_COMPOSE_PROFILE}]" in service
    assert "      - egress" in service and "      - backend" not in service
    assert "network_mode:" not in service
    assert "OPENAI_API_KEY:" not in service
    assert "source: openai_api_key" in service and "target: openai_api_key" in service
    assert 'user: "10002:10002"' in service and 'uid: "10002"' in service and 'gid: "10002"' in service
    assert "mode: 0400" in service
    assert "/private-corpus" not in service and "postgres" not in service and "qdrant" not in service
    for expected in (
        "read_only: true",
        "/tmp:rw,noexec,nosuid,size=64m",
        "no-new-privileges:true",
        "      - ALL",
        "pids_limit: 128",
        'restart: "no"',
        'command: ["python", "scripts/observe_next_private_speaker_review_adjudication_workspace.py"]',
    ):
        assert expected in service
