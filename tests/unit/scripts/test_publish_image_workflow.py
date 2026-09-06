from pathlib import Path

from cinegraph.config import (
    CORPUS_WORKER_EMBEDDING_INFERENCE_THREADS,
    CORPUS_WORKER_EMBEDDING_MAX_BATCH_SIZE,
)

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
    assert "--request GET" in text
    assert "--request HEAD" not in text
    assert text.count("--connect-timeout 10 --max-time 30") == 2
    assert "curl_status=$?" in text
    assert 'if [[ "$curl_status" -ne 0 ]]' in text
    assert "http_status" in text


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
    assert (
        text.count(
            "image: ${CINEGRAPH_IMAGE:?CINEGRAPH_IMAGE is required}@${CINEGRAPH_IMAGE_DIGEST:?CINEGRAPH_IMAGE_DIGEST is required}"
        )
        == 2
    )
    assert "image: &cinegraph-image" in text
    assert "CINEGRAPH_IMAGE_VERSION" not in text


def test_runtime_catalogue_mount_is_read_only_for_the_non_root_app() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "../knowledge/catalogue.json:/app/knowledge/catalogue.json:ro" in compose
    assert "USER cinegraph" in dockerfile
    assert "groupadd --system --gid 10001 cinegraph" in dockerfile
    assert "useradd --system --uid 10001" in dockerfile


def test_embedding_warmup_is_cache_only_and_egress_only() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    service = text[text.index("  warmup-embeddings:") : text.index("  migrate:")]

    assert "profiles: [warmup]" in service
    assert "image: *cinegraph-image" in service
    assert "app-cache:/home/cinegraph/.cache" in service
    assert "app-cache:/home/cinegraph/.cache:ro" not in service
    assert "- egress" in service
    assert "- backend" not in service
    assert "OPENAI_API_KEY" not in service
    assert "CINEGRAPH_DATABASE_URL" not in service
    assert "CINEGRAPH_QDRANT_URL" not in service
    assert "read_only: true" in service
    assert "no-new-privileges:true" in service
    assert "cap_drop:" in service and "ALL" in service
    assert 'restart: "no"' in service
    assert "scripts/warmup_embeddings.py" in service


def test_reviewed_corpus_ingestion_job_is_unprivileged_offline_and_bounded() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    environment = text[
        text.index("x-corpus-ingestion-environment:") : text.index("services:")
    ]
    service_start = text.index("  corpus-reviewed-ingestion:")
    service = text[service_start : text.index("  postgres:", service_start)]

    assert "profiles: [corpus-processing]" in service
    assert "image: *cinegraph-image" in service
    assert 'user: "10001:10001"' in service
    assert "- backend" in service
    assert "- egress" not in service
    assert "OPENAI_API_KEY" not in environment + service
    assert "CINEGRAPH_DATABASE_URL" not in environment + service
    assert "read_only: true" in service
    assert "/tmp:rw,noexec,nosuid,size=64m" in service
    assert "no-new-privileges:true" in service
    assert "cap_drop:" in service and "ALL" in service
    assert 'restart: "no"' in service
    assert "scripts/ingest_private_corpus_workspace.py" in service
    assert "/private-corpus" not in service
    assert 'mem_limit: ${CINEGRAPH_CORPUS_INGESTION_MEMORY_LIMIT:-1536m}' in service
    assert (
        f"CINEGRAPH_EMBEDDING_MAX_BATCH_SIZE: {CORPUS_WORKER_EMBEDDING_MAX_BATCH_SIZE}"
        in environment
    )
    assert (
        f"CINEGRAPH_EMBEDDING_INFERENCE_THREADS: {CORPUS_WORKER_EMBEDDING_INFERENCE_THREADS}"
        in environment
    )
    assert 'OMP_NUM_THREADS: "1"' in environment
    assert 'OPENBLAS_NUM_THREADS: "1"' in environment
    assert 'MKL_NUM_THREADS: "1"' in environment


def test_model_download_temp_and_huggingface_caches_use_persistent_volume() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    app_environment = text[text.index("x-app-environment:") : text.index("services:")]
    app_service = text[text.index("  app:") : text.index("  warmup-embeddings:")]
    warmup_service = text[text.index("  warmup-embeddings:") : text.index("  migrate:")]

    assert "HF_HOME: /home/cinegraph/.cache/huggingface" in text
    assert "HF_HUB_CACHE: /home/cinegraph/.cache/huggingface/hub" in text
    assert "HF_XET_CACHE: /home/cinegraph/.cache/huggingface/xet" in text
    assert "FASTEMBED_CACHE_PATH: /home/cinegraph/.cache/fastembed" in text
    assert "TMPDIR: /home/cinegraph/.cache/model-download-work" in text
    assert 'HF_HUB_DISABLE_XET: "1"' in text
    assert "environment: *model-warmup-environment" in warmup_service
    assert "TMPDIR" not in app_service
    assert 'HF_HUB_OFFLINE: "1"' in app_environment
    assert "app-cache:/home/cinegraph/.cache:ro" in app_service
    assert "HF_HUB_OFFLINE" not in warmup_service


def test_runtime_image_removes_unused_global_package_installers() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")

    assert "python -m pip uninstall --yes setuptools pip" in text
    assert "import ensurepip" in text
    assert 'ensurepip.__path__[0] + "/_bundled"' in text
    assert 'rm -rf "$ensurepip_bundle"' in text
