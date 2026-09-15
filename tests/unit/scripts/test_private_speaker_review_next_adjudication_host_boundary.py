from pathlib import Path

import pytest
from scripts import bootstrap_review_host
from scripts import private_speaker_review_next_adjudication_host_contract as host
from scripts import private_speaker_review_next_adjudication_submission_contract as contract


def test_host_contract_adds_one_finite_command_to_phase_70() -> None:
    assert host.REVIEW_NEXT_ADJUDICATION_COMMAND == contract.COMMAND
    assert host.REVIEW_NEXT_ADJUDICATION_HELPER_PATH.as_posix().endswith(
        "cinegraph-submit-next-private-speaker-review-adjudication"
    )
    assert (
        host.REVIEW_NEXT_ADJUDICATION_RECEIPTS_ROOT.name
        == "next-adjudication-submission-receipts"
    )
    assert host.SUDOERS_CONTENT.count("NOPASSWD:") == 8
    assert host.REVIEW_NEXT_ADJUDICATION_HELPER_PATH.as_posix() in host.SUDOERS_CONTENT
    assert "NOPASSWD: ALL" not in host.SUDOERS_CONTENT


def test_bootstrap_manages_the_new_root_only_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directories = {item.path for item in bootstrap_review_host.DIRECTORY_CONTRACT}
    files = {item.path for item in bootstrap_review_host.FILE_CONTRACT}

    assert host.REVIEW_NEXT_ADJUDICATION_RECEIPTS_ROOT in directories
    assert host.REVIEW_NEXT_ADJUDICATION_HELPER_PATH in files
    monkeypatch.setattr(
        bootstrap_review_host,
        "_read_source",
        lambda path: path.read_bytes(),
    )
    managed = bootstrap_review_host._managed_content(
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIP7fR75LrVcoQQVx+uQnTj2m1aNQdnziE/Km8eKh9XZk"
    )
    assert host.REVIEW_NEXT_ADJUDICATION_HELPER_PATH in managed
    managed_sudoers = managed[bootstrap_review_host.REVIEW_SUDOERS_PATH]
    assert managed_sudoers.startswith(host.SUDOERS_CONTENT.encode("utf-8"))
    assert b"cinegraph-observe-next-private-speaker-review-adjudication" in managed_sudoers
    assert b"NOPASSWD: ALL" not in managed_sudoers


def test_dispatcher_and_helper_are_fixed_and_fail_closed() -> None:
    dispatcher = Path("deploy/remote/review-dispatch.sh").read_text(encoding="utf-8")
    helper = Path(
        "deploy/remote/submit-next-private-speaker-review-adjudication.sh"
    ).read_text(encoding="utf-8")

    assert "speaker-review-submit-next-adjudication-v1)" in dispatcher
    assert (
        "sudo -n /usr/local/sbin/cinegraph-submit-next-private-speaker-review-adjudication"
        in dispatcher
    )
    assert "[[ $# -eq 0 ]]" in helper
    assert '[[ "${SUDO_USER-}" == "cinegraph-review" ]]' in helper
    assert 'python3 -I -S -B "$processor"' in helper
    assert "flock -n 8" in helper
    assert "flock -w 10 9" in helper
    assert "flock -n 7" in helper
    assert "${OPENAI_API_KEY" not in helper
    assert "--env OPENAI_API_KEY" not in helper
    assert "eval" not in helper and "bash -c" not in helper
    assert "origin/main" in helper
    assert helper.count("src/cinegraph/common/speaker_review_cost_policy.py") == 2
    assert '[[ "${lines[6]}" == "cinegraph-dev" ]]' in helper
    assert '[[ "${lines[7]}" == "10002:10002" ]]' in helper
    assert '[[ "${lines[16]}" == "cinegraph-dev_egress," ]]' in helper
    assert "OPENAI_API_KEY=" in helper
    assert (
        "^/opt/cinegraph/shared/private-corpus/dev/review-runs/sha256-[0-9a-f]{64}/review-runs/(speaker-review-[0-9a-f]{16})$"
        in helper
    )
    assert "$release_dir/shared/private-corpus/dev/review-runs" not in helper
    for trusted in (
        "scripts/run_private_speaker_review_next_adjudication.py",
        "scripts/submit_next_private_speaker_review_adjudication_workspace.py",
        "scripts/private_speaker_review_next_adjudication_submission_contract.py",
        "scripts/private_speaker_review_next_adjudication_host_contract.py",
        "scripts/run_private_speaker_review_first_adjudication_observation.py",
        "scripts/private_speaker_review_first_adjudication_observation_contract.py",
        "deploy/compose.yaml",
    ):
        assert trusted in helper


def _compose_service() -> str:
    compose = Path("deploy/compose.yaml").read_text(encoding="utf-8")
    return compose.split(
        "\n  corpus-speaker-review-submit-next-adjudication:", 1
    )[1].split("\n  postgres:", 1)[0]


def test_compose_worker_is_egress_only_secret_file_and_resource_bounded() -> None:
    service = _compose_service()

    assert "profiles: [corpus-speaker-review-submit-next-adjudication]" in service
    assert "      - egress" in service and "      - backend" not in service
    assert "network_mode:" not in service
    assert "OPENAI_API_KEY:" not in service
    assert "source: openai_api_key" in service
    assert "target: openai_api_key" in service
    assert 'uid: "10002"' in service and 'gid: "10002"' in service
    assert "mode: 0400" in service
    assert "CINEGRAPH_SPEAKER_REVIEW_RUNS_ROOT: /review-workspace/review-runs" in service
    assert "/private-corpus" not in service
    assert "postgres" not in service and "qdrant" not in service
    for expected in (
        'user: "10002:10002"',
        "read_only: true",
        "/tmp:rw,noexec,nosuid,size=64m",
        "no-new-privileges:true",
        "cap_drop:",
        "      - ALL",
        "pids_limit: 128",
        "mem_limit:",
        "cpus:",
        'restart: "no"',
        'command: ["python", "scripts/submit_next_private_speaker_review_adjudication_workspace.py"]',
    ):
        assert expected in service
