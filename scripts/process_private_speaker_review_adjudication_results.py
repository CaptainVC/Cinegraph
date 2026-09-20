"""Operator entry point for authorized adjudication-result processing."""

import os
import sys
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if os.fspath(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(_REPOSITORY_ROOT))

from scripts.private_speaker_review_adjudication_result_processing_client import (  # noqa: E402
    main,
)

if __name__ == "__main__":
    raise SystemExit(main())
