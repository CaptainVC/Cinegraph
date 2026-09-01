"""Materialize configured FastEmbed models and run a safe local sanity check."""

from __future__ import annotations

import logging
import os
import sys
import warnings

from cinegraph.config import (
    FASTEMBED_CACHE_DIR,
    FASTEMBED_CACHE_PATH_ENVIRONMENT_VARIABLE,
    HUGGINGFACE_HOME_DIR,
    HUGGINGFACE_HUB_CACHE_DIR,
    HUGGINGFACE_XET_CACHE_DIR,
    MODEL_DOWNLOAD_TMPDIR,
)

# Set these before importing FastEmbed/huggingface_hub: the latter reads its cache
# locations at import time.  All paths are on the persistent app-cache volume.
os.environ.setdefault("HF_HOME", HUGGINGFACE_HOME_DIR)
os.environ.setdefault("HF_HUB_CACHE", HUGGINGFACE_HUB_CACHE_DIR)
os.environ.setdefault("HF_XET_CACHE", HUGGINGFACE_XET_CACHE_DIR)
os.environ.setdefault(FASTEMBED_CACHE_PATH_ENVIRONMENT_VARIABLE, FASTEMBED_CACHE_DIR)
os.environ.setdefault("TMPDIR", MODEL_DOWNLOAD_TMPDIR)
os.environ.setdefault("TEMP", MODEL_DOWNLOAD_TMPDIR)
os.environ.setdefault("TMP", MODEL_DOWNLOAD_TMPDIR)
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from cinegraph.adapters.retrieval.fastembed_warmup import warmup_fastembed_models  # noqa: E402
from cinegraph.config import DEFAULT_EMBEDDING_CONFIGURATION


def main() -> int:
    """Return a sanitized status without exposing provider errors or payloads."""

    previous_disable_level = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=(
                    r"^Cannot enable progress bars: environment variable "
                    r"`HF_HUB_DISABLE_PROGRESS_BARS=1` is set and has priority\.$"
                ),
                category=UserWarning,
                module=r"^huggingface_hub\.utils\.tqdm$",
            )
            result = warmup_fastembed_models(
                DEFAULT_EMBEDDING_CONFIGURATION,
                FASTEMBED_CACHE_DIR,
            )
    except Exception:
        print(
            "Embedding model warmup failed: local model preparation or sanity check failed.",
            file=sys.stderr,
        )
        return 1
    finally:
        logging.disable(previous_disable_level)

    print(
        "Embedding model warmup passed: "
        f"dense_model={result.dense_model} "
        f"dense_dimension={result.dense_dimension} "
        f"sparse_model={result.sparse_model}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
