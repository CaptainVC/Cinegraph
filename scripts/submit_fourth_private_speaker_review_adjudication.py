"""Operator entry point for one authorized fourth-adjudication submission."""

from __future__ import annotations

from scripts import run_private_speaker_review_fourth_adjudication as coordinator


def main() -> int:
    return coordinator.main()


if __name__ == "__main__":
    raise SystemExit(main())
