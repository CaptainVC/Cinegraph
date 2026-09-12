from pathlib import Path


def _service() -> str:
    compose = Path("deploy/compose.yaml").read_text(encoding="utf-8")
    return compose.split("\n  corpus-speaker-review-process-primary-results:", 1)[
        1
    ].split("\n  corpus-speaker-review-submit-first-adjudication:", 1)[0]


def test_primary_result_processing_worker_is_offline_and_isolated() -> None:
    service = _service()
    assert "profiles: [corpus-speaker-review-process-primary-results]" in service
    assert "network_mode: none" in service
    assert "OPENAI_API_KEY" not in service
    assert "openai_api_key" not in service
    assert "/review-workspace/review-runs" in service
    assert (
        'command: ["python", "scripts/process_private_speaker_review_results_workspace.py"]'
        in service
    )


def test_primary_result_processing_worker_has_fixed_identity_and_bounds() -> None:
    service = _service()
    for expected in (
        'user: "10002:10002"',
        "read_only: true",
        "no-new-privileges:true",
        "cap_drop:",
        "- ALL",
        "pids_limit: 128",
        "mem_limit:",
        "cpus:",
        'restart: "no"',
        "/tmp:rw,noexec,nosuid,size=64m",
    ):
        assert expected in service
