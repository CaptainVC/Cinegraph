from typing import Final

from cinegraph.config.speaker_review_filesystem import (
    PRIVATE_RECORD_MAX_BYTES,
    PRIVATE_REQUEST_MAX_BYTES,
)

SUBMISSION_RECORD_MAX_BYTES: Final = PRIVATE_RECORD_MAX_BYTES
SUBMISSION_REQUEST_MAX_BYTES: Final = PRIVATE_REQUEST_MAX_BYTES
SUBMISSION_STAGES: Final = frozenset({"primary", "adjudication", "final-review"})
SUBMISSION_KINDS: Final = frozenset({"intent", "completed"})
SUBMISSION_SCHEMA_VERSION: Final = 1


def submission_filename(stage: str, part: int, kind: str) -> str:
    return f".{stage}-part-{part:04d}-submission-{kind}.json"
