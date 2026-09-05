"""Runtime policy for the restricted reviewed-corpus worker."""

from typing import Final

# Keep these filters exact and scoped to the reporting module.  Unexpected worker
# warnings must remain visible on stderr so the root processor can reject them.
CORPUS_WORKER_WARNING_FILTERS: Final[tuple[tuple[str, str], ...]] = (
    (
        r"^Api key is used with an insecure connection\.$",
        r"^cinegraph\.bootstrap\.composition_root$",
    ),
    (
        r"^Cannot enable progress bars: environment variable "
        r"`HF_HUB_DISABLE_PROGRESS_BARS=1` is set and has priority\.$",
        r"^huggingface_hub\.utils\.tqdm$",
    ),
)

# Keep the restricted one-shot independent of operator model-runtime overrides.
CORPUS_WORKER_EMBEDDING_MAX_BATCH_SIZE: Final[int] = 8
CORPUS_WORKER_EMBEDDING_INFERENCE_THREADS: Final[int] = 1
