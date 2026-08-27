"""Centralized publication policy for structured series metadata."""

from typing import Final

SERIES_METADATA_PENDING_DIRECTORY: Final[str] = "series-metadata/pending"
SERIES_METADATA_APPROVED_DIRECTORY: Final[str] = "series-metadata/approved"
SERIES_METADATA_ARTWORK_DIRECTORY: Final[str] = "series-metadata/artwork"
SERIES_METADATA_REVIEWER_ID: Final[str] = "cinegraph:deterministic-tvmaze-review-v1"
SERIES_METADATA_POSTER_MAX_BYTES: Final[int] = 5 * 1024 * 1024
SERIES_METADATA_POSTER_TIMEOUT_SECONDS: Final[float] = 10.0
SERIES_METADATA_POSTER_CONNECT_TIMEOUT_SECONDS: Final[float] = 5.0
SERIES_METADATA_POSTER_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {"image/jpeg", "image/png", "image/webp"}
)
