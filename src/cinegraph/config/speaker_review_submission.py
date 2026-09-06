from typing import Final

SUBMISSION_RECORD_MAX_BYTES: Final = 4096
SUBMISSION_REQUEST_MAX_BYTES: Final = 32 * 1024 * 1024
SUBMISSION_STAGES: Final = frozenset({"primary", "adjudication", "final-review"})
SUBMISSION_KINDS: Final = frozenset({"intent", "completed"})
SUBMISSION_SCHEMA_VERSION: Final = 1


def submission_filename(stage: str, part: int, kind: str) -> str:
    return f".{stage}-part-{part:04d}-submission-{kind}.json"
