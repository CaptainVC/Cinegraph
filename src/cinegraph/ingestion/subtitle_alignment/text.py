import re
import unicodedata


# Normalize text for case-insensitive, accent-insensitive dialogue matching.
def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


# Collapse speaker whitespace and normalize names to uppercase for matching.
def normalize_speaker(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().upper()
