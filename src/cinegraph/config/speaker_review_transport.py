"""Transport bounds for irreversible speaker-review submissions."""

from typing import Final

# Submission retries belong to the durable journal, not the HTTP client.
SPEAKER_REVIEW_SUBMISSION_MAX_RETRIES: Final[int] = 0
SPEAKER_REVIEW_SUBMISSION_TIMEOUT_SECONDS: Final[float] = 60.0
