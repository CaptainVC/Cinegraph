import re


class SrtPatterns:

    CUE_SEPARATOR = re.compile(r"\r?\n\s*\r?\n")

    TIMECODE = re.compile(
        r"^(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+"
        r"(?P<end>\d{2}:\d{2}:\d{2},\d{3})(?:\s+.*)?$"
    )

    VERIFIED_SPEAKER_LABEL_PATTERN = re.compile(
        r"^(?P<speaker>[A-Za-z][A-Za-z -]{0,48}):\s*(?P<text>.+)$"
    )

    STYLE_TAG_PATTERN = re.compile(r"<[^>]+>")

    WHITESPACE_PATTERN = re.compile(r"\s+")
