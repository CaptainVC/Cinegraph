from dataclasses import dataclass

from cinegraph.domain.models.transcript.transcript_segment import TranscriptSegment

@dataclass(frozen=True, slots=True)
class ParsedSrtCue:
    cue_number: int
    start_ms: int
    end_ms: int
    lines: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class TranscriptIngestionReport:
    source_path: str
    cue_count: int
    segment_count: int
    multi_speaker_cue_count: int
    overlap_count: int
    style_removed_segment_count: int
    skipped_non_dialogue_cue_count: int = 0

@dataclass(frozen=True, slots=True)
class TranscriptIngestionResult:
    segments: tuple[TranscriptSegment, ...]
    report: TranscriptIngestionReport
