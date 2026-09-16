from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts import bootstrap_review_host
from scripts import private_speaker_review_fourth_adjudication_host_contract as phase75
from scripts import private_speaker_review_fourth_adjudication_observation_contract as contract
from scripts import private_speaker_review_fourth_adjudication_observation_host_contract as host


def test_host_policy_adds_exactly_one_finite_sudo_command_after_phase75() -> None:
    assert host.REVIEW_FOURTH_ADJUDICATION_OBSERVATION_COMMAND == contract.COMMAND
    assert host.SUDOERS_CONTENT.startswith(phase75.SUDOERS_CONTENT)
    assert host.SUDOERS_CONTENT.count("NOPASSWD:") == phase75.SUDOERS_CONTENT.count("NOPASSWD:") + 1
    assert host.REVIEW_FOURTH_ADJUDICATION_OBSERVATION_HELPER_PATH.as_posix() in host.SUDOERS_CONTENT
    assert "NOPASSWD: ALL" not in host.SUDOERS_CONTENT
    assert host.REVIEW_FOURTH_ADJUDICATION_SUBMISSION_RECEIPTS_ROOT.name == "fourth-adjudication-submission-receipts"
    assert host.REVIEW_FOURTH_ADJUDICATION_OBSERVATION_RECEIPTS_ROOT.name == "fourth-adjudication-observation-receipts"


def test_dispatcher_bootstrap_and_helper_are_exactly_bound() -> None:
    dispatcher = Path("deploy/remote/review-dispatch.sh").read_text(encoding="utf-8")
    helper = Path("deploy/remote/observe-fourth-private-speaker-review-adjudication.sh").read_text(encoding="utf-8")
    assert f"{contract.COMMAND})" in dispatcher
    assert "sudo -n /usr/local/sbin/cinegraph-observe-fourth-private-speaker-review-adjudication" in dispatcher
    assert "[[ $# -eq 0 ]]" in helper
    assert '[[ "${SUDO_USER-}" == "cinegraph-review" ]]' in helper
    assert 'python3 -I -S -B "$processor"' in helper
    assert helper.index('exec 8>"$TRANSFER_LOCK"') < helper.index('exec 9>"$DEPLOYMENT_LOCK"') < helper.index('exec 7>"$SPEAKER_REVIEW_LOCK"')
    assert "flock -n 8" in helper and "flock -w 10 9" in helper and "flock -n 7" in helper
    assert "eval" not in helper and "bash -c" not in helper
    assert "--env OPENAI_API_KEY" not in helper and "${OPENAI_API_KEY" not in helper
    assert "origin/main" in helper
    assert helper.count("src/cinegraph/common/speaker_review_cost_policy.py") == 2
    assert "scripts/run_private_speaker_review_fourth_adjudication_observation.py" in helper
    assert "scripts/observe_fourth_private_speaker_review_adjudication_workspace.py" in helper
    assert "deploy/compose.yaml" in helper
    directories = {item.path for item in bootstrap_review_host.DIRECTORY_CONTRACT}
    files = {item.path for item in bootstrap_review_host.FILE_CONTRACT}
    assert host.REVIEW_FOURTH_ADJUDICATION_OBSERVATION_RECEIPTS_ROOT in directories
    assert host.REVIEW_FOURTH_ADJUDICATION_OBSERVATION_HELPER_PATH in files


def test_compose_observer_is_egress_only_unprivileged_and_secret_file_only() -> None:
    compose = Path("deploy/compose.yaml").read_text(encoding="utf-8")
    service = compose.split("\n  corpus-speaker-review-observe-fourth-adjudication:", 1)[1].split("\n  postgres:", 1)[0]
    assert f"profiles: [{host.REVIEW_FOURTH_ADJUDICATION_OBSERVATION_COMPOSE_PROFILE}]" in service
    assert "      - egress" in service and "      - backend" not in service
    assert "network_mode:" not in service
    assert "OPENAI_API_KEY:" not in service
    assert "source: openai_api_key" in service and "target: openai_api_key" in service
    assert 'user: "10002:10002"' in service and 'uid: "10002"' in service and 'gid: "10002"' in service
    assert "mode: 0400" in service
    assert "/private-corpus" not in service and "postgres" not in service and "qdrant" not in service
    for expected in (
        "read_only: true", "/tmp:rw,noexec,nosuid,size=64m", "no-new-privileges:true",
        "      - ALL", "pids_limit: 128", 'restart: "no"',
        'command: ["python", "scripts/observe_fourth_private_speaker_review_adjudication_workspace.py"]',
    ):
        assert expected in service


def test_isolated_import_closure_is_trusted_and_tracked() -> None:
    project = Path(__file__).parents[3]
    code = f"""
import json, pathlib, sys
root = pathlib.Path({str(project)!r})
sys.path.insert(0, str(root)); sys.path.insert(0, str(root / 'scripts')); sys.path.insert(0, str(root / 'src'))
import scripts.run_private_speaker_review_fourth_adjudication_observation
loaded = sorted(str(pathlib.Path(m.__file__).resolve().relative_to(root)).replace(chr(92), '/')
                for m in sys.modules.values() if getattr(m, '__file__', None)
                and pathlib.Path(m.__file__).resolve().is_relative_to(root))
print(json.dumps(loaded))
"""
    result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code], cwd=project, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    loaded = json.loads(result.stdout)
    helper = Path("deploy/remote/observe-fourth-private-speaker-review-adjudication.sh").read_text(encoding="utf-8")
    variable_bound = {
        "scripts/run_private_speaker_review_fourth_adjudication_observation.py",
        "scripts/private_speaker_review_fourth_adjudication_observation_contract.py",
        "scripts/private_speaker_review_fourth_adjudication_observation_host_contract.py",
    }
    for path in loaded:
        assert path in variable_bound or f'"$release_dir/{path}"' in helper
        assert path in variable_bound or (f"    {path} " + chr(92)) in helper
    assert "nonexistent" not in helper
