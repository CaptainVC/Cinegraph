
from pathlib import Path

from cinegraph.ingestion.transcript_srt.parser import read_srt_text


class LocalSubtitleTextReader:

    # Reads and returns the requested source content.
    def read_text(self, source_locator: str) -> str:
        return read_srt_text(Path(source_locator))
