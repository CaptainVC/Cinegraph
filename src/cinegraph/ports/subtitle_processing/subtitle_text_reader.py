from typing import Protocol


class SubtitleTextReader(Protocol):

    def read_text(self, source_locator: str) -> str:
        ...
