from __future__ import annotations

from pathlib import Path

from scripts import private_corpus_host_contract as host_contract


def test_speaker_review_service_is_offline_and_unprivileged() -> None:
    compose = Path("deploy/compose.yaml").read_text(encoding="utf-8")
    service = compose.split("  corpus-speaker-review-prepare:", 1)[1].split(
        "\n  postgres:", 1
    )[0]

    assert "profiles: [corpus-speaker-review]" in service
    assert 'user: "10002:10002"' in service
    assert "network_mode: none" in service
    assert "    networks:" not in service
    assert "    ports:" not in service
    assert "    env_file:" not in service
    assert "    volumes:" not in service
    assert "read_only: true" in service
    assert "no-new-privileges:true" in service
    assert "cap_drop:" in service and "      - ALL" in service
    assert 'restart: "no"' in service
    assert (
        'command: ["python", "scripts/prepare_private_speaker_review_workspace.py"]'
        in service
    )
    assert 'HF_HUB_OFFLINE: "1"' in service
    assert "OPENAI_API_KEY" not in service
    assert "CINEGRAPH_QDRANT_URL" not in service
    assert "CINEGRAPH_DATABASE_URL" not in service


def test_bootstrap_contract_contains_private_review_roots_and_helper() -> None:
    paths = {
        item.path
        for item in __import__(
            "scripts.bootstrap_corpus_host", fromlist=["FILE_CONTRACT"]
        ).FILE_CONTRACT
    }
    assert host_contract.SPEAKER_REVIEW_HELPER_PATH in paths
    assert host_contract.SPEAKER_REVIEW_ROOT.parent == host_contract.DEV_PRIVATE_CORPUS_ROOT
    assert host_contract.SPEAKER_REVIEW_SOURCE_ROOT.parent == host_contract.SPEAKER_REVIEW_ROOT
    assert host_contract.SPEAKER_REVIEW_RECEIPTS_ROOT.parent == host_contract.SPEAKER_REVIEW_ROOT
