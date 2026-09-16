import json
import subprocess
import sys
from pathlib import Path

from scripts import bootstrap_review_host
from scripts import private_speaker_review_fourth_adjudication_host_contract as host
from scripts import private_speaker_review_fourth_adjudication_submission_contract as contract
from scripts import run_private_speaker_review_fourth_adjudication as coordinator
from scripts.private_speaker_review_third_adjudication_observation_host_contract import (
    SUDOERS_CONTENT as phase74_sudoers,
)


def test_host_policy_adds_one_finite_command_after_phase74() -> None:
    assert host.REVIEW_FOURTH_ADJUDICATION_COMMAND == contract.COMMAND
    assert host.SUDOERS_CONTENT.startswith(phase74_sudoers)
    assert host.SUDOERS_CONTENT.count("NOPASSWD:") == phase74_sudoers.count("NOPASSWD:") + 1
    assert host.REVIEW_FOURTH_ADJUDICATION_HELPER_PATH.as_posix() in host.SUDOERS_CONTENT
    assert "NOPASSWD: ALL" not in host.SUDOERS_CONTENT
    assert host.REVIEW_FOURTH_ADJUDICATION_RECEIPTS_ROOT.name == (
        "fourth-adjudication-submission-receipts"
    )


def test_dispatcher_helper_and_bootstrap_are_root_only_and_fail_closed() -> None:
    dispatcher = Path("deploy/remote/review-dispatch.sh").read_text(encoding="utf-8")
    quality = Path(".github/workflows/quality.yml").read_text(encoding="utf-8")
    helper = Path(
        "deploy/remote/submit-fourth-private-speaker-review-adjudication.sh"
    ).read_text(encoding="utf-8")
    assert f"{contract.COMMAND})" in dispatcher
    assert (
        "sudo -n /usr/local/sbin/cinegraph-submit-fourth-private-speaker-review-adjudication"
        in dispatcher
    )
    assert "[[ $# -eq 0 ]]" in helper
    assert '[[ "${SUDO_USER-}" == "cinegraph-review" ]]' in helper
    assert 'python3 -I -S -B "$processor"' in helper
    assert "flock -n 8" in helper and "flock -w 10 9" in helper and "flock -n 7" in helper
    assert "eval" not in helper and "bash -c" not in helper
    assert "--env OPENAI_API_KEY" not in helper and "${OPENAI_API_KEY" not in helper
    assert "origin/main" in helper
    assert helper.count("scripts/run_private_speaker_review_next_adjudication.py") == 2
    assert helper.count("src/cinegraph/common/speaker_review_cost_policy.py") == 2
    assert "NEXT_ADJUDICATION_RECEIPTS_ROOT" in helper
    assert "NEXT_ADJUDICATION_OBSERVATION_RECEIPTS_ROOT" in helper
    assert "THIRD_ADJUDICATION_RECEIPTS_ROOT" in helper
    assert "THIRD_ADJUDICATION_OBSERVATION_RECEIPTS_ROOT" in helper
    assert "deploy/remote/submit-fourth-private-speaker-review-adjudication.sh" in quality
    directories = {item.path for item in bootstrap_review_host.DIRECTORY_CONTRACT}
    files = {item.path for item in bootstrap_review_host.FILE_CONTRACT}
    assert host.REVIEW_FOURTH_ADJUDICATION_RECEIPTS_ROOT in directories
    assert host.REVIEW_FOURTH_ADJUDICATION_HELPER_PATH in files


def test_helper_mount_is_the_exact_digest_selected_run() -> None:
    helper = Path(
        "deploy/remote/submit-fourth-private-speaker-review-adjudication.sh"
    ).read_text(encoding="utf-8")
    assert (
        "^/opt/cinegraph/shared/private-corpus/dev/review-runs/"
        "sha256-[0-9a-f]{64}/review-runs/(speaker-review-[0-9a-f]{16})$"
        in helper
    )
    assert (
        '"$review_mount_source|/review-workspace/review-runs/${BASH_REMATCH[1]}|true"'
        in helper
    )


def test_helper_trusts_complete_isolated_root_import_closure() -> None:
    project = Path(__file__).parents[3]
    code = f"""
import json, pathlib, sys
root = pathlib.Path({str(project)!r})
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / 'scripts'))
sys.path.insert(0, str(root / 'src'))
import scripts.run_private_speaker_review_fourth_adjudication
loaded = sorted(
    str(pathlib.Path(module.__file__).resolve().relative_to(root)).replace(chr(92), '/')
    for module in sys.modules.values()
    if getattr(module, '__file__', None)
    and pathlib.Path(module.__file__).resolve().is_relative_to(root)
)
print(json.dumps(loaded))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    loaded = json.loads(result.stdout)
    helper = Path(
        "deploy/remote/submit-fourth-private-speaker-review-adjudication.sh"
    ).read_text(encoding="utf-8")
    variable_bound = {
        "scripts/private_speaker_review_fourth_adjudication_host_contract.py",
        "scripts/private_speaker_review_fourth_adjudication_submission_contract.py",
        "scripts/run_private_speaker_review_fourth_adjudication.py",
    }
    for path in loaded:
        assert path in variable_bound or f'"$release_dir/{path}"' in helper
        assert path in variable_bound or f"    {path} \\" in helper


def test_compose_worker_is_egress_only_unprivileged_and_secret_file_only() -> None:
    compose = Path("deploy/compose.yaml").read_text(encoding="utf-8")
    service = compose.split(
        "\n  corpus-speaker-review-submit-fourth-adjudication:", 1
    )[1].split("\n  corpus-speaker-review-observe-next-adjudication:", 1)[0]
    assert f"profiles: [{host.REVIEW_FOURTH_ADJUDICATION_COMPOSE_PROFILE}]" in service
    assert "      - egress" in service and "      - backend" not in service
    assert "network_mode:" not in service
    assert "OPENAI_API_KEY:" not in service
    assert "source: openai_api_key" in service and "target: openai_api_key" in service
    assert 'user: "10002:10002"' in service and "mode: 0400" in service
    assert "/private-corpus" not in service and "postgres" not in service and "qdrant" not in service
    assert "<<: *model-cache-environment" in service
    for name, value in coordinator.WORKER_STATIC_ENVIRONMENT.items():
        assert f"{name}: {value}" in compose or f'{name}: "{value}"' in compose
    for expected in (
        "read_only: true",
        "/tmp:rw,noexec,nosuid,size=64m",
        "no-new-privileges:true",
        "      - ALL",
        "pids_limit: 128",
        'restart: "no"',
        'command: ["python", "scripts/submit_fourth_private_speaker_review_adjudication_workspace.py"]',
    ):
        assert expected in service
