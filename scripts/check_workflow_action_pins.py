"""Check that remote GitHub Actions are pinned to immutable commit SHAs."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Final, Iterable

WORKFLOW_ROOT: Final = Path(".github/workflows")
USES_PATTERN: Final = re.compile(r"^\s*(?:-\s*)?uses:\s*(?P<reference>[^\s#]+)")
COMMIT_SHA_PATTERN: Final = re.compile(r"^[0-9a-f]{40}$")
CONTAINER_DIGEST_PATTERN: Final = re.compile(r"^docker://.+@sha256:[0-9a-f]{64}$")
LOCAL_ACTION_PREFIX: Final = "./"


def _workflow_paths(paths: Iterable[str]) -> list[Path]:
    supplied = [Path(path) for path in paths]
    if supplied:
        return supplied
    if not WORKFLOW_ROOT.exists():
        return []
    return sorted(path for path in WORKFLOW_ROOT.rglob("*") if path.suffix in {".yml", ".yaml"})


def _unpinned_actions(path: Path) -> list[str]:
    issues: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = USES_PATTERN.match(line)
        if match is None:
            continue
        reference = match.group("reference").strip("\"'")
        if reference.startswith(LOCAL_ACTION_PREFIX):
            continue
        if reference.startswith("docker://"):
            if not CONTAINER_DIGEST_PATTERN.fullmatch(reference):
                issues.append(
                    f"{path}:{line_number}: container action ref must use a sha256 digest"
                )
            continue
        _, separator, revision = reference.rpartition("@")
        if not separator or not COMMIT_SHA_PATTERN.fullmatch(revision):
            issues.append(f"{path}:{line_number}: action ref must be a 40-character commit SHA")
    return issues


def main(argv: list[str] | None = None) -> int:
    issues = [issue for path in _workflow_paths(argv or []) for issue in _unpinned_actions(path)]
    if issues:
        print("\n".join(issues), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
