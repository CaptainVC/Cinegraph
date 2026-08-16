from dataclasses import dataclass

from cinegraph.domain.models.source.source_version import SourceVersion
from cinegraph.domain.models.transcript.transcript_segment import TranscriptSegment


@dataclass(frozen=True, slots=True)
class IndexTranscriptSegmentsCommand:
    source_version: SourceVersion
    segments: tuple[TranscriptSegment, ...]


@dataclass(frozen=True, slots=True)
class IndexTranscriptSegmentsResult:
    indexed_segment_count: int
