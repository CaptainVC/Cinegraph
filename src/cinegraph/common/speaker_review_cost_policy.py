"""Stdlib-only review cost policy shared with isolated host coordinators."""

from typing import Final

ESTIMATED_CHARACTERS_PER_TOKEN: Final = 3
BATCH_DISCOUNT_MULTIPLIER: Final = 0.5
MAXIMUM_RUN_COST_USD: Final = 5.0
BATCH_ENDPOINT: Final = "/v1/responses"
BATCH_COMPLETION_WINDOW: Final = "24h"
SPEAKER_REVIEW_SCHEMA_VERSION: Final = 5
SPEAKER_REVIEW_PROMPT_VERSION: Final = "speaker-review-v1"
SPEAKER_PRIMARY_REVIEW_MODEL: Final = "gpt-5.6-luna"
SPEAKER_ADJUDICATION_MODEL: Final = "gpt-5.6-terra"
SPEAKER_FINAL_REVIEW_MODEL: Final = "gpt-5.6-sol"
# A defensive host-boundary ceiling. Normal runs contain only a handful of
# parts; this prevents an unauthenticated state document from driving an
# unbounded range expansion before the application validator is available.
MAXIMUM_REVIEW_PART_COUNT: Final = 1_024
MODEL_TOKEN_PRICES: Final = {
    "gpt-5.6-luna": (0.20, 1.20),
    "gpt-5.6-terra": (2.00, 12.00),
    SPEAKER_FINAL_REVIEW_MODEL: (5.00, 30.00),
}
