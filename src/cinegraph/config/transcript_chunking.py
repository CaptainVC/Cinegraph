from dataclasses import dataclass

from cinegraph.common.error_messages import TranscriptErrorMessages

TRANSCRIPT_INDEX_REVISION = "transcript-chunk-v1"


@dataclass(frozen=True, slots=True)
class TranscriptChunkingConfiguration:
    revision: str = TRANSCRIPT_INDEX_REVISION
    max_segments: int = 8
    overlap_segments: int = 2
    max_characters: int = 1200
    max_duration_ms: int = 90_000
    max_inter_segment_gap_ms: int = 10_000

    def __post_init__(self) -> None:
        if (
            not isinstance(self.revision, str)
            or not self.revision
            or self.revision.strip() != self.revision
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 1
                for value in (
                    self.max_segments,
                    self.max_characters,
                    self.max_duration_ms,
                    self.max_inter_segment_gap_ms,
                )
            )
            or isinstance(self.overlap_segments, bool)
            or not isinstance(self.overlap_segments, int)
            or self.overlap_segments < 0
            or self.overlap_segments >= self.max_segments
        ):
            raise ValueError(TranscriptErrorMessages.TRANSCRIPT_CHUNK_CONFIGURATION_INVALID)


DEFAULT_TRANSCRIPT_CHUNKING_CONFIGURATION = TranscriptChunkingConfiguration()
