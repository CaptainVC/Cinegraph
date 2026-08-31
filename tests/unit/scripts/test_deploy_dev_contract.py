import json
import shutil
import subprocess
from pathlib import Path

import pytest

WORKFLOW = Path(".github/workflows/deploy-dev.yml")
REMOTE_SCRIPT = Path("deploy/remote/deploy-dev.sh")
DISPATCH_SCRIPT = Path("deploy/remote/deploy-dispatch.sh")
WORKFLOW_RUN_FIXTURE = Path("tests/fixtures/deploy_dev/publish_image_completed.json")


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
    assert '--source-digest "$RELEASE_SHA"' in text
    assert "--deny-self-hosted-runners" in text
    assert '--signer-workflow "$GITHUB_REPOSITORY/.github/workflows/publish-image.yml"' in text
    assert "GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in text
    assert "    env:\n      GITHUB_TOKEN:" not in text
    assert "    env:\n      CINEGRAPH_DEV_HOST:" not in text


def test_nested_publisher_event_fixture_preserves_inactive_repository_gate() -> None:
    payload = json.loads(WORKFLOW_RUN_FIXTURE.read_text(encoding="utf-8"))
    text = WORKFLOW.read_text(encoding="utf-8")

    assert payload["workflow_run"]["name"] == "Publish immutable image"
    assert payload["workflow_run"]["event"] == "workflow_run"
    assert (
        payload["workflow_run"]["head_repository"]["full_name"]
        == payload["repository"]["full_name"]
    )
    assert "CINEGRAPH_DEV_DEPLOY_ENABLED" not in payload
    assert "vars.CINEGRAPH_DEV_DEPLOY_ENABLED == 'true'" in text


def test_dev_deployment_uses_pinned_ssh_without_tofu_or_private_data_transfer() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "StrictHostKeyChecking=yes" in text
    assert "UserKnownHostsFile=" in text
    assert "IdentitiesOnly=yes" in text
    assert "BatchMode=yes" in text
    assert '[[ "$CINEGRAPH_DEV_USER" == "cinegraph-deploy" ]]' in text
    assert '[[ "$CINEGRAPH_DEV_USER" =~' not in text
    assert "from scripts.dev_host_contract import known_hosts_line" in text
    assert "one canonical Ed25519 host-key line" in text
    assert "ssh-keyscan" not in text
    assert "umask 077" in text
    assert 'mktemp -d "$RUNNER_TEMP/cinegraph-ssh.XXXXXX"' in text
    assert "trap cleanup_ssh EXIT" in text
    assert 'remote_command="deploy $RELEASE_SHA $IMAGE_DIGEST"' in text
    assert '"$remote_command"' in text
    assert "< deploy/remote/deploy-dev.sh" not in text
    assert "bash -s" not in text
    assert "OPENAI_API_KEY" not in text
    assert "knowledge" not in text
    assert "vars.CINEGRAPH_DEV_DEPLOY_ENABLED == 'true'" in text


def test_remote_script_is_bounded_to_dev_digest_promotion() -> None:
    text = REMOTE_SCRIPT.read_text(encoding="utf-8")

    assert 'readonly DEPLOY_ROOT="/opt/cinegraph"' in text
    assert 'readonly ENV_FILE="/etc/cinegraph/dev.env"' in text
    assert 'readonly IMAGE_NAME="ghcr.io/captainvc/cinegraph"' in text
    assert 'readonly REPOSITORY_SOURCE="https://github.com/CaptainVC/Cinegraph"' in text
    assert "[[ $EUID -eq 0 ]]" in text
    assert "deployment helper accepts no command-line arguments" in text
    assert "IFS= read -r release_sha" in text
    assert "unexpected extra input" in text
    assert "check_root_path" in text
    assert '[[ "$(uname -m)" == "x86_64" ]]' in text
    assert "stat -c '%a'" in text
    assert 'check_root_path "$ENV_FILE" file 600' in text
    assert '! -L "$path"' in text
    assert '! -L "$release_dir"' in text
    assert "git clone --quiet --no-checkout" in text
    assert "checkout --quiet --detach" in text
    assert "merge-base --is-ancestor" in text
    assert "refs/remotes/origin/main" in text
    assert "normalize_public_catalogue_permissions" in text
    assert "knowledge/catalogue.json" in text
    assert 'git -C "$checkout_dir" ls-files --error-unmatch -- knowledge/catalogue.json' in text
    assert '[[ -d "$knowledge_dir" && ! -L "$knowledge_dir" ]]' in text
    assert '[[ -f "$catalogue_path" && ! -L "$catalogue_path" ]]' in text
    assert '[[ "$(stat -c \'%u:%g\' "$catalogue_path")" == "0:0" ]]' in text
    assert 'chmod 0644 "$catalogue_path"' in text
    assert '[[ "$(stat -c \'%a\' "$catalogue_path")" == "644" ]]' in text
    normalize = text.index('normalize_public_catalogue_permissions "$release_dir"')
    checkout_verification = text.index('verify_release_checkout "$release_dir"')
    image_pull = text.index('"${compose[@]}" pull app postgres qdrant')
    migration = text.index('"${compose[@]}" --profile migration run --rm migrate')
    assert checkout_verification < normalize < image_pull < migration
    assert "CINEGRAPH_IMAGE_DIGEST=" in text
    assert "CINEGRAPH_RELEASE_SHA=" in text
    assert "candidate_env" in text
    assert "previous_env" in text
    assert "flock -n 9" in text
    assert "sleep 5" in text
    assert "command -v curl" in text
    assert "command -v python3" in text
    assert "DOCKER_CONFIG" in text
    assert "mktemp -d /run/cinegraph-docker.XXXXXX" in text
    assert text.index("previous Dev environment backup exists") < text.index(
        '"${compose[@]}" pull app'
    )
    assert "CINEGRAPH_ENV_FILE=" in text
    assert "validate_vps_runtime.py" in text
    assert "pull app postgres qdrant" in text
    assert "docker image inspect" in text
    assert "docker container create" in text
    assert "docker container inspect" in text
    assert "docker container rm --force" in text
    assert "org.opencontainers.image.source" in text
    assert "org.opencontainers.image.revision" in text
    assert "pulled image revision does not match" in text
    assert "--profile migration run --rm migrate" in text
    assert "--profile warmup run --rm warmup-embeddings" in text
    image_verification = text.index("pulled image revision does not match")
    warmup = text.index('"${compose[@]}" --profile warmup run --rm warmup-embeddings')
    dependencies = text.index('"${compose[@]}" up -d postgres qdrant')
    assert image_verification < warmup < dependencies
    assert "--profile provisioning run --rm provision-qdrant" in text
    assert "docker compose down" not in text
    assert "docker compose build" not in text
    assert "rm -rf" not in text
    assert "health/ready" in text

    bash = shutil.which("bash")
    if bash is not None:
        result = subprocess.run(
            [bash, "-n", str(REMOTE_SCRIPT)], check=False, capture_output=True, text=True
        )
        if result.returncode != 0 and "CreateProcessCommon" in result.stderr:
            pytest.skip("Windows WSL bash shim is unavailable")
        assert result.returncode == 0


def test_forced_command_dispatcher_has_no_shell_expansion_or_privilege_bypass() -> None:
    text = DISPATCH_SCRIPT.read_text(encoding="utf-8")

    assert '[[ "$(id -un)" == "cinegraph-deploy" ]]' in text
    assert "[[ $# -eq 0 ]]" in text
    assert "SSH_ORIGINAL_COMMAND" in text
    assert "^deploy\\ ([0-9a-f]{40})\\ (sha256:[0-9a-f]{64})$" in text
    assert "sudo -n /usr/local/sbin/cinegraph-deploy-dev" in text
    assert "eval" not in text
    assert "bash -c" not in text
    assert "docker" not in text

    bash = shutil.which("bash")
    if bash is not None:
        result = subprocess.run(
            [bash, "-n", str(DISPATCH_SCRIPT)], check=False, capture_output=True, text=True
        )
        if result.returncode != 0 and "CreateProcessCommon" in result.stderr:
            pytest.skip("Windows WSL bash shim is unavailable")
        assert result.returncode == 0
