import re

from cinegraph.domain.models.transcript.transcript_segment import (
    TranscriptSegment,
)


TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


# Normalizes the supplied value for consistent processing.
def normalize_tokens(text: str) -> frozenset[str]:
    return frozenset(
        TOKEN_PATTERN.findall(text.casefold())
    )


# Processes the supplied lexical score values.
def lexical_score(
    query: str,
    segment: TranscriptSegment,
) -> float:
    query_tokens = normalize_tokens(query)
    segment_tokens = normalize_tokens(segment.text)

    if not query_tokens or not segment_tokens:
        return 0.0

    matched_tokens = query_tokens & segment_tokens

    return len(matched_tokens) / len(query_tokens)
