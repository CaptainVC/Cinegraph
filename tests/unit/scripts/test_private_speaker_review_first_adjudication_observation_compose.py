from pathlib import Path


def _service() -> str:
    compose = Path("deploy/compose.yaml").read_text(encoding="utf-8")
    return compose.split("\n  corpus-speaker-review-observe-first-adjudication:", 1)[1].split(
        "\n  postgres:", 1
    )[0]


def test_first_adjudication_observer_has_a_dedicated_egress_only_profile() -> None:
    service = _service()

    assert "profiles: [corpus-speaker-review-observe-first-adjudication]" in service
    assert "      - egress" in service
    assert "      - backend" not in service
    assert "network_mode:" not in service
    assert (
        'command: ["python", '
        '"scripts/observe_first_private_speaker_review_adjudication_workspace.py"]' in service
    )


def test_first_adjudication_observer_exposes_only_the_review_mount_and_secret_file() -> None:
    service = _service()

    assert "OPENAI_API_KEY:" not in service
    assert "source: openai_api_key" in service
    assert "target: openai_api_key" in service
    assert 'uid: "10002"' in service and 'gid: "10002"' in service
    assert "mode: 0400" in service
    assert "CINEGRAPH_SPEAKER_REVIEW_RUNS_ROOT: /review-workspace/review-runs" in service
    assert "/private-corpus" not in service
    assert "postgres" not in service and "qdrant" not in service


def test_first_adjudication_observer_is_unprivileged_and_resource_bounded() -> None:
    service = _service()

    for expected in (
        'user: "10002:10002"',
        "read_only: true",
        "/tmp:rw,noexec,nosuid,size=64m",
        "no-new-privileges:true",
        "cap_drop:",
        "      - ALL",
        "pids_limit: 128",
        "mem_limit:",
        "cpus:",
        'restart: "no"',
    ):
        assert expected in service
