import shutil
import subprocess
from pathlib import Path

import pytest

WORKFLOW = Path(".github/workflows/deploy-dev.yml")
REMOTE_SCRIPT = Path("deploy/remote/deploy-dev.sh")


def test_dev_deployment_is_activation_gated_and_first_party_only() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "workflows: [Publish immutable image]" in text
    assert "vars.CINEGRAPH_DEV_DEPLOY_ENABLED == 'true'" in text
    assert "github.event.workflow_run.event == 'workflow_run'" in text
    assert "github.event.workflow_run.event == 'push'" not in text
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert "github.event.workflow_run.head_repository.full_name == github.repository" in text
    assert "github.event.workflow_run.head_branch == 'main'" in text
    assert "environment: dev" in text
    assert "workflow_dispatch" not in text
    assert "packages: read" in text
    assert "attestations: read" in text
    assert "python -m scripts.resolve_ghcr_digest" in text
    assert "--source-ref refs/heads/main" in text
    assert "--source-digest \"$RELEASE_SHA\"" in text
    assert "--deny-self-hosted-runners" in text
    assert "--signer-workflow \"$GITHUB_REPOSITORY/.github/workflows/publish-image.yml\"" in text
    assert "GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in text
    assert "    env:\n      GITHUB_TOKEN:" not in text
    assert "    env:\n      CINEGRAPH_DEV_HOST:" not in text


def test_dev_deployment_uses_pinned_ssh_without_tofu_or_private_data_transfer() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "StrictHostKeyChecking=yes" in text
    assert "UserKnownHostsFile=" in text
    assert "IdentitiesOnly=yes" in text
    assert "BatchMode=yes" in text
    assert "ssh-keyscan" not in text
    assert "umask 077" in text
    assert "mktemp -d \"$RUNNER_TEMP/cinegraph-ssh.XXXXXX\"" in text
    assert "trap cleanup_ssh EXIT" in text
    assert "< deploy/remote/deploy-dev.sh" in text
    assert "OPENAI_API_KEY" not in text
    assert "knowledge" not in text
    assert "vars.CINEGRAPH_DEV_DEPLOY_ENABLED == 'true'" in text


def test_remote_script_is_bounded_to_dev_digest_promotion() -> None:
    text = REMOTE_SCRIPT.read_text(encoding="utf-8")

    assert 'readonly DEPLOY_ROOT="/opt/cinegraph"' in text
    assert 'readonly ENV_FILE="/etc/cinegraph/dev.env"' in text
    assert 'readonly IMAGE_NAME="ghcr.io/captainvc/cinegraph"' in text
    assert '[[ "$(uname -m)" == "x86_64" ]]' in text
    assert "stat -c '%a'" in text
    assert "mode 0600" in text
    assert '! -L "$ENV_FILE"' in text
    assert '! -L "$release_dir"' in text
    assert "git clone --quiet --no-checkout" in text
    assert "checkout --quiet --detach" in text
    assert "CINEGRAPH_IMAGE_DIGEST=" in text
    assert "CINEGRAPH_RELEASE_SHA=" in text
    assert "candidate_env" in text
    assert "previous_env" in text
    assert "flock -n 9" in text
    assert "sleep 5" in text
    assert "command -v curl" in text
    assert "command -v python3" in text
    assert text.index('previous Dev environment backup exists') < text.index('"${compose[@]}" pull app')
    assert "CINEGRAPH_ENV_FILE=" in text
    assert "validate_vps_runtime.py" in text
    assert "pull app postgres qdrant" in text
    assert "--profile migration run --rm migrate" in text
    assert "--profile provisioning run --rm provision-qdrant" in text
    assert "docker compose down" not in text
    assert "docker compose build" not in text
    assert "rm -rf" not in text
    assert "health/ready" in text

    bash = shutil.which("bash")
    if bash is not None:
        result = subprocess.run([bash, "-n", str(REMOTE_SCRIPT)], check=False, capture_output=True, text=True)
        if result.returncode != 0 and "CreateProcessCommon" in result.stderr:
            pytest.skip("Windows WSL bash shim is unavailable")
        assert result.returncode == 0
