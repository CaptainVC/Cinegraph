"""Operator entry point for authorized final-review part-one submission."""

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if os.fspath(_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(_ROOT))

from scripts.private_speaker_review_final_review_submission_client import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
