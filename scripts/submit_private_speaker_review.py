"""Operator entry point for one authorized primary speaker-review submission."""

import os
import sys
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if os.fspath(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(_REPOSITORY_ROOT))

from scripts.private_speaker_review_submission_client import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
