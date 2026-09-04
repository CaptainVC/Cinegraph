from dataclasses import dataclass

from cinegraph.common.private_corpus_policy import (
    ALIGNED_DIRECTORY_NAME,
    ALIGNED_SUBTITLE_SUFFIX,
    REVIEW_LEDGER_FILENAME,
    REVIEWED_DIRECTORY_NAME,
    REVIEWED_SUBTITLE_SUFFIX,
    SEASON_DIRECTORY_SUFFIX,
)


@dataclass(frozen=True, slots=True)
class CorpusLayoutConfiguration:
    season_directory_suffix: str = SEASON_DIRECTORY_SUFFIX
    reviewed_directory_name: str = REVIEWED_DIRECTORY_NAME
    aligned_directory_name: str = ALIGNED_DIRECTORY_NAME
    review_ledger_filename: str = REVIEW_LEDGER_FILENAME
    raw_subtitle_suffix: str = ".en.srt"
    reviewed_subtitle_suffix: str = REVIEWED_SUBTITLE_SUFFIX
    aligned_subtitle_suffix: str = ALIGNED_SUBTITLE_SUFFIX


DEFAULT_CORPUS_LAYOUT = CorpusLayoutConfiguration()
