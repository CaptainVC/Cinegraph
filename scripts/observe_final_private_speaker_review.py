"""Operator entry point for one final-review part-one observation."""

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if os.fspath(_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(_ROOT))

from scripts.private_speaker_review_final_review_observation_client import (  # noqa: E402
    main,
)

if __name__ == "__main__":
    raise SystemExit(main())
