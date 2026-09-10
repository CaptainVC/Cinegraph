from pathlib import Path

from scripts import private_speaker_review_next_primary_observation_contract as contract
from scripts import private_speaker_review_next_primary_observation_host_contract as host


def test_host_contract_is_a_fourth_finite_command() -> None:
    assert host.REVIEW_NEXT_OBSERVATION_COMMAND == contract.COMMAND
    assert host.REVIEW_NEXT_OBSERVATION_HELPER_PATH.as_posix().endswith(
        "cinegraph-observe-next-private-speaker-review"
    )
    assert host.REVIEW_NEXT_OBSERVATION_RECEIPTS_ROOT.name == "observation-receipts"
    assert host.SUDOERS_CONTENT.count("NOPASSWD:") == 4
    assert host.REVIEW_NEXT_OBSERVATION_HELPER_PATH.as_posix() in host.SUDOERS_CONTENT
    assert "NOPASSWD: ALL" not in host.SUDOERS_CONTENT


def test_dispatcher_and_helper_are_fixed_and_fail_closed() -> None:
    dispatcher = Path("deploy/remote/review-dispatch.sh").read_text(encoding="utf-8")
    helper = Path("deploy/remote/observe-next-private-speaker-review.sh").read_text(
        encoding="utf-8"
    )
    assert "speaker-review-observe-next-primary-v1)" in dispatcher
    assert "sudo -n /usr/local/sbin/cinegraph-observe-next-private-speaker-review" in dispatcher
    assert "[[ $# -eq 0 ]]" in helper
    assert '[[ "${SUDO_USER-}" == "cinegraph-review" ]]' in helper
    assert 'python3 -I -S -B "$processor"' in helper
    assert "flock -n 8" in helper and "flock -w 10 9" in helper and "flock -n 7" in helper
    assert "${OPENAI_API_KEY" not in helper
    assert "--env OPENAI_API_KEY" not in helper
    assert "eval" not in helper and "bash -c" not in helper
    assert "origin/main" in helper
    assert '[[ "${lines[6]}" == "cinegraph-dev" ]]' in helper
    assert '[[ "${lines[7]}" == "10002:10002" ]]' in helper
    assert '[[ "${lines[16]}" == "cinegraph-dev_egress," ]]' in helper
    assert "OPENAI_API_KEY=" in helper
    assert (
        "^/opt/cinegraph/shared/private-corpus/dev/review-runs/sha256-[0-9a-f]{64}/review-runs$"
        in helper
    )
    assert "$release_dir/shared/private-corpus/dev/review-runs" not in helper
