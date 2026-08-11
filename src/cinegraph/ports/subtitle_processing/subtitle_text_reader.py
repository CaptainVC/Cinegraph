from typing import Protocol


class SubtitleTextReader(Protocol):

    # Read subtitle source text from the supplied locator.
    def read_text(self, source_locator: str) -> str:
        ...
