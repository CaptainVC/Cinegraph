import re


EPISODE_HEADER_PATTERN = re.compile(
    r"^\s*(?P<season>\d+)x(?P<episode>\d+)\s*:?\s+(?P<title>.+?)\s*$",
    re.IGNORECASE,
)
SPEAKER_LINE_PATTERN = re.compile(
    r"^(?P<speaker>[A-Za-z][A-Za-z -]{0,48})\s*:\s*(?P<text>.+)$"
)
TIMECODE_PATTERN = re.compile(
    r"^\d{2}:\d{2}:\d{2},\d{3}\s+-->\s+\d{2}:\d{2}:\d{2},\d{3}(?:\s+.*)?$"
)
EXISTING_LABEL_PATTERN = re.compile(r"^(?:[A-Z][A-Z ]*\??|UNKNOWN):\s+")
SUBTITLE_EPISODE_PATTERN = re.compile(r"\b(?P<season>\d+)x(?P<episode>\d{2})\b")
NON_DIALOGUE_NOISE_PATTERN = re.compile(
    r"^(?:sync(?:ed)?\s+by|corrected\s+by|resync(?:ed)?\s+by|subtitle(?:s)?\s+by)\b",
    re.IGNORECASE,
)
BRACKETED_STAGE_DIRECTION_PATTERN = re.compile(r"\s*\[[^\]]+\]\s*")

SUBTITLE_ENCODINGS = ("utf-8-sig", "cp1252")
TITLE_CARD_TEXTS = frozenset({"MODERN FAMILY", "OPENING CREDITS", "END CREDITS"})
SYNC_CREDIT_PREFIXES = ("sync by", "synced by", "sync:")
