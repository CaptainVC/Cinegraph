from __future__ import annotations

from pathlib import Path

from scripts import private_speaker_review_final_review_host_contract as host
from scripts import private_speaker_review_final_review_submission_contract as contract


def test_host_contract_and_dispatcher_are_single_finite_boundary() -> None:
    dispatcher = Path("deploy/remote/review-dispatch.sh").read_text(encoding="utf-8")
    helper = Path("deploy/remote/submit-final-private-speaker-review.sh").read_text(
        encoding="utf-8"
    )
    assert host.REVIEW_FINAL_REVIEW_COMMAND == contract.COMMAND
    assert f"{contract.COMMAND})" in dispatcher
    assert host.REVIEW_FINAL_REVIEW_HELPER_PATH.as_posix() in dispatcher
    assert "[[ $# -eq 0 ]]" in helper
    assert "origin/main" in helper
    assert "python3 -I -S -B" in helper
    assert "flock -n 8" in helper and "flock -w 10 9" in helper and "flock -n 7" in helper
    assert 'find "$release_dir" -xdev -type d -print0' in helper
    assert 'git -C "$release_dir" ls-files -z' in helper
    assert "stat -c '%h'" in helper
    assert "eval" not in helper and "bash -c" not in helper


def test_compose_service_is_egress_only_and_unprivileged() -> None:
    compose = Path("deploy/compose.yaml").read_text(encoding="utf-8")
    service = compose.split(
        "\n  corpus-speaker-review-submit-final-review:", 1
    )[1].split("\n  postgres:", 1)[0]
    assert f"profiles: [{host.REVIEW_FINAL_REVIEW_COMPOSE_PROFILE}]" in service
    assert "      - egress" in service and "      - backend" not in service
    assert "source: openai_api_key" in service and "target: openai_api_key" in service
    assert 'user: "10002:10002"' in service
    assert "read_only: true" in service
    assert "/tmp:rw,noexec,nosuid,size=64m" in service
    assert "no-new-privileges:true" in service
    assert "      - ALL" in service
    assert "pids_limit: 128" in service
    assert 'restart: "no"' in service
    assert "submit_final_private_speaker_review_workspace.py" in service
    dockerfile = Path("deploy/Dockerfile").read_text(encoding="utf-8")
    assert "/review-workspace/review-runs" in dockerfile
    assert "chown 10002:10002 /review-workspace/review-runs" in dockerfile
    assert "chmod 0700 /review-workspace/review-runs" in dockerfile


def test_root_client_and_worker_do_not_expose_provider_fields() -> None:
    worker = Path(
        "scripts/submit_final_private_speaker_review_workspace.py"
    ).read_text(encoding="utf-8")
    root = Path("scripts/run_private_speaker_review_final_review.py").read_text(
        encoding="utf-8"
    )
    client = Path(
        "scripts/private_speaker_review_final_review_submission_client.py"
    ).read_text(encoding="utf-8")
    assert "final-review-part-0001-requests.jsonl" in worker
    assert "_replay_workflow" in worker
    assert "read_stable_openai_secret" in worker
    assert "provider access disabled" in worker
    assert "phase78_processing_intent_sha256" in root
    assert "phase78_processing_receipt_sha256" in root
    assert "OUTPUT_MAX_BYTES" in client
