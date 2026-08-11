from typing import Protocol


class SubtitleTextReader(Protocol):

    # Reads and returns the requested source content.
    def read_text(self, source_locator: str) -> str:
        ...
