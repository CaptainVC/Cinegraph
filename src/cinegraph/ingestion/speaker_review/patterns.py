import re

UNCERTAIN_SPEAKER_LABEL_PATTERN = re.compile(
    r"^(?P<speaker>[A-Za-z][A-Za-z .'-]{0,48})\?:\s*(?P<text>.+)$"
)

ANY_SPEAKER_LABEL_PATTERN = re.compile(
    r"^(?P<speaker>[A-Za-z][A-Za-z .'-]{0,48})(?P<uncertain>\?)?:\s*(?P<text>.+)$"
)
