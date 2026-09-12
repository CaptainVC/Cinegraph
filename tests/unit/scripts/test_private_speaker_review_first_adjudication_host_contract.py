from __future__ import annotations

from pathlib import Path

from scripts import private_speaker_review_first_adjudication_host_contract as contract


def test_first_adjudication_adds_exactly_one_sudo_grant_to_phase68() -> None:
    grant = (
        f'{contract.REVIEW_USER} ALL=(root) NOPASSWD: '
        f'{contract.REVIEW_FIRST_ADJUDICATION_HELPER_PATH.as_posix()} ""\n'
    )
    assert contract.SUDOERS_CONTENT == contract.PHASE68_SUDOERS_CONTENT + grant
    assert contract.SUDOERS_CONTENT.count(contract.REVIEW_FIRST_ADJUDICATION_HELPER_PATH.as_posix()) == 1
    assert "NOPASSWD: ALL" not in contract.SUDOERS_CONTENT


def test_first_adjudication_contract_is_single_service_egress_secret_boundary() -> None:
    compose = Path("deploy/compose.yaml").read_text(encoding="utf-8")
    marker = f"  {contract.REVIEW_FIRST_ADJUDICATION_COMPOSE_SERVICE}:"
    assert compose.count(marker) == 1
    service = compose.split(marker, 1)[1].split("\n  postgres:", 1)[0]

    assert f"profiles: [{contract.REVIEW_FIRST_ADJUDICATION_COMPOSE_PROFILE}]" in service
    assert 'user: "10002:10002"' in service
    assert "    networks:\n      - egress" in service
    assert "      - backend" not in service
    assert "network_mode:" not in service
    assert "    volumes:" not in service
    assert "    secrets:" in service
    assert "source: openai_api_key" in service
    assert "target: openai_api_key" in service
    assert "read_only: true" in service
    assert "no-new-privileges:true" in service
    assert "      - ALL" in service
    assert 'restart: "no"' in service
    assert (
        'command: ["python", "scripts/submit_first_private_speaker_review_adjudication_workspace.py"]'
        in service
    )
    assert "CINEGRAPH_DATABASE_URL" not in service
    assert "CINEGRAPH_QDRANT_URL" not in service
    assert "CINEGRAPH_IDENTITY_DATABASE_PATH" not in service
    assert (
        "CINEGRAPH_SPEAKER_REVIEW_RUNS_ROOT: /review-workspace/review-runs" in service
    )
