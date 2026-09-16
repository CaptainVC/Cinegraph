"""Operator entry point for one authorized fourth-adjudication observation."""

import os
import sys
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if os.fspath(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(_REPOSITORY_ROOT))

from scripts.private_speaker_review_fourth_adjudication_observation_client import (  # noqa: E402
    main,
)

if __name__ == "__main__":
    raise SystemExit(main())
