"""Run Cinegraph's complete local quality contract in a reproducible order."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(label: str, command: list[str]) -> None:
    print(f"\n==> {label}: {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> None:
    python = sys.executable
    _run("lint", [python, "-m", "ruff", "check", "."])
    _run("type check", [python, "-m", "mypy"])
    _run(
        "tests and branch coverage",
        [
            python,
            "-m",
            "pytest",
            "-m",
            "not e2e",
            "--cov",
            "--cov-report=term-missing",
            "--cov-report=xml",
            "--cov-report=json",
        ],
    )
    _run("deterministic retrieval evaluation", [python, "scripts/run_synthetic_evaluation.py"])
    _run("pre-commit", [python, "-m", "pre_commit", "run", "--all-files"])
    _run("package build", ["uv", "build", "--wheel"])
    print("\nQuality contract passed.", flush=True)


if __name__ == "__main__":
    main()
