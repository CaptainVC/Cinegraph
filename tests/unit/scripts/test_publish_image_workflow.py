from pathlib import Path

WORKFLOW = Path(".github/workflows/publish-image.yml")
QUALITY_WORKFLOW = Path(".github/workflows/quality.yml")
COMPOSE = Path("deploy/compose.yaml")
DOCKERFILE = Path("deploy/Dockerfile")


def test_publish_workflow_is_successful_quality_completion_only_on_main() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_run:" in text
    assert "workflows: [Quality]" in text
    assert "types: [completed]" in text
    assert "github.event.workflow_run.event == 'push'" in text
    assert "github.event.workflow_run.head_repository.full_name == github.repository" in text
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert "github.event.workflow_run.head_branch == 'main'" in text
    assert "workflow_dispatch" not in text
    assert "manual-main-guard" not in text
    assert "MANUAL_SHA" not in text
    assert "ref: ${{ steps.release.outputs.sha }}" in text


def test_publish_workflow_contains_only_immutable_release_tag_and_attestation() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "IMAGE_NAME: ghcr.io/captainvc/cinegraph" in text
    assert "tags: ${{ env.IMAGE_NAME }}:${{ steps.release.outputs.tag }}" in text
    assert "tag=sha-$release_sha" in text
    assert ":latest" not in text
    assert "latest-" not in text
    assert "platforms: linux/amd64" in text
    assert "sbom: true" in text
    assert "provenance: mode=max" in text
    assert "actions/attest-build-provenance@" in text
    assert "push-to-registry: true" in text
    assert "secrets.GITHUB_TOKEN" in text
    assert "packages: write" in text
    assert "id-token: write" in text
    assert "attestations: write" in text
    assert "umask 077" in text
    assert "trap 'rm -f \"$token_file\"' EXIT" in text


def test_quality_compose_contract_scans_the_built_image() -> None:
    text = QUALITY_WORKFLOW.read_text(encoding="utf-8")

    assert "aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25" in text
    assert "image-ref: cinegraph:ci" in text
    assert "severity: HIGH,CRITICAL" in text
    assert "scanners: vuln" in text
    assert "ignore-unfixed: true" in text
    assert "timeout: 10m" in text


def test_runtime_compose_has_no_build_fallback_and_uses_digest_for_all_app_jobs() -> None:
    text = COMPOSE.read_text(encoding="utf-8")

    assert "    build:" not in text
    assert text.count(
        "image: ${CINEGRAPH_IMAGE:?CINEGRAPH_IMAGE is required}@${CINEGRAPH_IMAGE_DIGEST:?CINEGRAPH_IMAGE_DIGEST is required}"
    ) == 3
    assert "CINEGRAPH_IMAGE_VERSION" not in text


def test_runtime_image_removes_unused_global_package_installers() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")

    assert "python -m pip uninstall --yes setuptools pip" in text
    assert "import ensurepip" in text
    assert 'ensurepip.__path__[0] + "/_bundled"' in text
    assert 'rm -rf "$ensurepip_bundle"' in text
