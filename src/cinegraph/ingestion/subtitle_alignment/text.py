import re
import unicodedata


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


def normalize_speaker(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().upper()
