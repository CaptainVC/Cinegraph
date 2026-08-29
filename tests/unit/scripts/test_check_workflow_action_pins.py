from pathlib import Path

from scripts.check_workflow_action_pins import _unpinned_actions

COMMIT_SHA = "a" * 40
CONTAINER_DIGEST = "b" * 64


def _write_workflow(path: Path, uses_lines: list[str]) -> None:
    path.write_text("\n".join(uses_lines) + "\n", encoding="utf-8")


def test_accepts_multiline_and_one_line_steps_pinned_to_commit_sha(tmp_path: Path) -> None:
    workflow = tmp_path / "quality.yml"
    _write_workflow(
        workflow,
        [
            f"        uses: actions/checkout@{COMMIT_SHA} # release",
            f"      - uses: actions/setup-python@{COMMIT_SHA}",
        ],
    )

    assert _unpinned_actions(workflow) == []


def test_rejects_tagged_action_in_one_line_step(tmp_path: Path) -> None:
    workflow = tmp_path / "quality.yml"
    _write_workflow(workflow, ["      - uses: actions/checkout@v7"])

    assert _unpinned_actions(workflow) == [
        f"{workflow}:1: action ref must be a 40-character commit SHA"
    ]


def test_allows_local_action_without_remote_revision(tmp_path: Path) -> None:
    workflow = tmp_path / "quality.yml"
    _write_workflow(workflow, ["      - uses: ./.github/actions/local-check"])

    assert _unpinned_actions(workflow) == []


def test_requires_container_action_to_use_sha256_digest(tmp_path: Path) -> None:
    workflow = tmp_path / "quality.yml"
    _write_workflow(
        workflow,
        [
            "      - uses: docker://alpine:3.22",
            f"      - uses: docker://alpine@sha256:{CONTAINER_DIGEST}",
        ],
    )

    assert _unpinned_actions(workflow) == [
        f"{workflow}:1: container action ref must use a sha256 digest"
    ]
