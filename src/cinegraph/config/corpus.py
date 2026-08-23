from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CorpusLayoutConfiguration:
    season_directory_suffix: str = " - season {season_number:01}.en"
    reviewed_directory_name: str = "reviewed"
    aligned_directory_name: str = "script-aligned"
    review_ledger_filename: str = "review-ledger.json"
    raw_subtitle_suffix: str = ".en.srt"
    reviewed_subtitle_suffix: str = ".reviewed.srt"
    aligned_subtitle_suffix: str = ".script-aligned.srt"


DEFAULT_CORPUS_LAYOUT = CorpusLayoutConfiguration()
