
from pathlib import Path

from cinegraph.ingestion.transcript_srt.parser import read_srt_text


class LocalSubtitleTextReader:

    # Read and parse canonical subtitle text from the local source path.
    def read_text(self, source_locator: str) -> str:
        return read_srt_text(Path(source_locator))
