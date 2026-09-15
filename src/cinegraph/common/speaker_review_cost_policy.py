"""Stdlib-only review cost policy shared with isolated host coordinators."""

from typing import Final

ESTIMATED_CHARACTERS_PER_TOKEN: Final = 3
BATCH_DISCOUNT_MULTIPLIER: Final = 0.5
MAXIMUM_RUN_COST_USD: Final = 5.0
BATCH_ENDPOINT: Final = "/v1/responses"
BATCH_COMPLETION_WINDOW: Final = "24h"
MODEL_TOKEN_PRICES: Final = {
    "gpt-5.6-luna": (0.20, 1.20),
    "gpt-5.6-terra": (2.00, 12.00),
    "gpt-5.6-sol": (5.00, 30.00),
}
