"""Transport bounds for irreversible speaker-review submissions."""

from typing import Final

# Submission retries belong to the durable journal, not the HTTP client.
SPEAKER_REVIEW_SUBMISSION_MAX_RETRIES: Final[int] = 0
SPEAKER_REVIEW_SUBMISSION_TIMEOUT_SECONDS: Final[float] = 60.0

# Observation is a read-only, bounded operation.  A single app-layer
# retrieve/download call is used so an ambiguous provider response cannot
# silently become a second observation; the SDK timeout remains finite.
SPEAKER_REVIEW_OBSERVATION_MAX_RETRIES: Final[int] = 0
SPEAKER_REVIEW_OBSERVATION_TIMEOUT_SECONDS: Final[float] = 60.0
