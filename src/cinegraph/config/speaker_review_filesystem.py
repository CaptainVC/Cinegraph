"""Central filesystem policy for private speaker-review runs.

This module intentionally contains only layout, schema, and size policy.  The
filesystem operations live in ``cinegraph.ingestion.speaker_review.private_io``
so callers cannot accidentally duplicate the physical-path checks.
"""

from dataclasses import dataclass
from typing import Final

RUN_STATE_FILENAME: Final = "run-state.json"
SOURCE_MANIFEST_FILENAME: Final = "source-manifest.json"
REQUEST_MANIFEST_FILENAME: Final = "request-manifest.json"
CANDIDATES_FILENAME: Final = "candidates.jsonl"
RUN_DIRECTORY_NAME: Final = "review-runs"
RUN_ID_PREFIX: Final = "speaker-review-"

PRIVATE_ARTIFACT_MAX_BYTES: Final = 64 * 1024 * 1024
PRIVATE_SOURCE_MAX_BYTES: Final = 32 * 1024 * 1024
PRIVATE_REQUEST_MAX_BYTES: Final = 32 * 1024 * 1024
PRIVATE_RECORD_MAX_BYTES: Final = 4096
PRIVATE_PATH_MAX_BYTES: Final = 240
PRIVATE_NAME_MAX_BYTES: Final = 120
PRIVATE_FILESYSTEM_SCHEMA_VERSION: Final = 1
WINDOWS_INVALID_FILENAME_CHARACTERS: Final = frozenset('<>:"|?*')
WINDOWS_RESERVED_BASENAMES: Final = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
)


@dataclass(frozen=True, slots=True)
class SpeakerReviewFilesystemConfiguration:
    """Centralized filesystem bounds used by the speaker-review workflow."""

    run_directory_name: str = RUN_DIRECTORY_NAME
    run_id_prefix: str = RUN_ID_PREFIX
    filesystem_schema_version: int = PRIVATE_FILESYSTEM_SCHEMA_VERSION
    artifact_max_bytes: int = PRIVATE_ARTIFACT_MAX_BYTES
    source_max_bytes: int = PRIVATE_SOURCE_MAX_BYTES
    request_max_bytes: int = PRIVATE_REQUEST_MAX_BYTES
    record_max_bytes: int = PRIVATE_RECORD_MAX_BYTES
    path_max_bytes: int = PRIVATE_PATH_MAX_BYTES
    name_max_bytes: int = PRIVATE_NAME_MAX_BYTES
    private_directory_mode: int = 0o700
    private_file_mode: int = 0o600


DEFAULT_SPEAKER_REVIEW_FILESYSTEM_CONFIGURATION = (
    SpeakerReviewFilesystemConfiguration()
)
