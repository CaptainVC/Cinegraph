"""Operator entry point for one private-corpus processing request."""

import os
import sys
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if os.fspath(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(_REPOSITORY_ROOT))

from scripts.process_private_corpus_client import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
