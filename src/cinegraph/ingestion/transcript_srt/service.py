
from pathlib import Path
from uuid import UUID

from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.common.error_messages import SubtitleErrorMessages
from cinegraph.domain.enums.enum import Language, RightsStatus
from cinegraph.domain.models.transcript.speaker_candidate import SpeakerCandidate
from cinegraph.domain.models.transcript.transcript_segment import TranscriptSegment
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef
from cinegraph.ingestion.transcript_srt.constants import SrtConstants
from cinegraph.ingestion.transcript_srt.models import (
     ParsedSrtCue,
     TranscriptIngestionReport,
     TranscriptIngestionResult,
)
from cinegraph.ingestion.transcript_srt.parser import parse_srt, read_srt_text
from cinegraph.ingestion.transcript_srt.patterns import SrtPatterns


# Processes the supplied ingest finalized srt values.
def ingest_finalized_srt(
     *,
     source_path: Path,
     source_version_id: UUID,
     episode: EpisodeRef,
     language: Language,
     rights_status: RightsStatus,
) -> TranscriptIngestionResult:
     # Read and parse the finalized subtitle source into structured cues.
    cues = parse_srt(read_srt_text(source_path))

     # Convert every parsed cue into a canonical transcript segment and report.
    segments = tuple(
        _to_transcript_segment(
            cue=cue,
            source_version_id=source_version_id,
            episode=episode,
            language=language,
            rights_status=rights_status,
        )
        for cue in cues
    )

    return TranscriptIngestionResult(
          segments=segments,
          report=_build_report(
               source_path=source_path,
               cues=cues,
               segments=segments,
          ),
    )

# Processes the supplied ingest finalized srt text values.
def ingest_finalized_srt_text(
     *,
     source_text: str,
     source_path: Path,
     source_version_id: UUID,
     episode: EpisodeRef,
     language: Language,
     rights_status: RightsStatus,
) -> TranscriptIngestionResult:
     # Parse caller-supplied subtitle text into structured cues.
    cues = parse_srt(source_text)

     # Convert the parsed cues and summarize the resulting transcript.
    segments = tuple(
        _to_transcript_segment(
            cue=cue,
            source_version_id=source_version_id,
            episode=episode,
            language=language,
            rights_status=rights_status,
        )
        for cue in cues
    )

    return TranscriptIngestionResult(
          segments=segments,
          report=_build_report(
               source_path=source_path,
               cues=cues,
               segments=segments,
          ),
    )


# Processes the supplied to transcript segment values.
def _to_transcript_segment(
     *,
     cue: ParsedSrtCue,
     source_version_id: UUID,
     episode: EpisodeRef,
     language: Language,
     rights_status: RightsStatus,
) -> TranscriptSegment:
     # Validate each labeled subtitle line and collect canonical dialogue and speakers.
     speaker_candidates: list[SpeakerCandidate] = []
     dialogue_parts: list[str] = []
     style_removed = False

     for line in cue.lines:
          speaker_match = SrtPatterns.VERIFIED_SPEAKER_LABEL_PATTERN.fullmatch(line)
          if speaker_match is None:
               raise ValueError(
                    SubtitleErrorMessages.SRT_CUE_REQUIRES_VERIFIED_SPEAKER_LABEL.format(
                         cue_number=cue.cue_number
                    )
               )

          speaker_name = _normalize_speaker_name(speaker_match.group("speaker"))
          dialogue, removed_styles = _canonicalize_dialogue(
               speaker_match.group("text")
          )
          if not dialogue:
               raise ValueError(
                    SubtitleErrorMessages.SRT_CUE_MUST_HAVE_DIALOGUE.format(
                         cue_number=cue.cue_number
                    )
               )

          dialogue_parts.append(dialogue)
          style_removed = style_removed or removed_styles

          if any(candidate.name == speaker_name for candidate in speaker_candidates):
               continue

          speaker_candidates.append(
               SpeakerCandidate(
                    speaker_id=_speaker_id(
                         series_id=episode.series_id,
                         speaker_name=speaker_name,
                    ),
                    name=speaker_name,
                    confidence=SrtConstants.VERIFIED_SPEAKER_CONFIDENCE,
               )
          )

     # Combine normalized dialogue and derive stable identifiers for the segment.
     text = " ".join(dialogue_parts)
     return TranscriptSegment(
          segment_id=_segment_id(
               source_version_id=source_version_id,
               episode=episode,
               cue=cue,
               text=text,
          ),
          source_version_id=source_version_id,
          episode=episode,
          start_ms=cue.start_ms,
          end_ms=cue.end_ms,
          text=text,
          language=language,
          rights_status=rights_status,
          style_removed=style_removed,
          speaker_candidates=tuple(speaker_candidates),
     )


# Processes the supplied canonicalize dialogue values.
def _canonicalize_dialogue(value: str) -> tuple[str, bool]:
     without_styles = SrtPatterns.STYLE_TAG_PATTERN.sub(" ", value)
     text = SrtPatterns.WHITESPACE_PATTERN.sub(" ", without_styles).strip()
     return text, without_styles != value


# Normalizes the supplied value for consistent processing.
def _normalize_speaker_name(value: str) -> str:
     return SrtPatterns.WHITESPACE_PATTERN.sub(" ", value).strip().upper()


# Processes the supplied speaker id values.
def _speaker_id(
     *,
     series_id: UUID,
     speaker_name: str,
) -> UUID:
    return IdentifierGenerator.speaker_id(series_id, speaker_name)


# Processes the supplied segment id values.
def _segment_id(
     *,
     source_version_id: UUID,
     episode: EpisodeRef,
     cue: ParsedSrtCue,
     text: str,
) -> UUID:
    return IdentifierGenerator.transcript_segment_id(
        source_version_id,
        episode.episode_id,
        cue.cue_number,
        cue.start_ms,
        cue.end_ms,
        text,
    )


# Builds and returns the requested structure.
def _build_report(
     *,
     source_path: Path,
     cues: tuple[ParsedSrtCue, ...],
     segments: tuple[TranscriptSegment, ...],
) -> TranscriptIngestionReport:
     # Summarize cue counts, speaker multiplicity, overlaps, and removed styling.
     overlap_count = sum(
          current.start_ms < previous.end_ms
          for previous, current in zip(segments, segments[1:])
     )
     return TranscriptIngestionReport(
          source_path=str(source_path),
          cue_count=len(cues),
          segment_count=len(segments),
          multi_speaker_cue_count=sum(
               len(segment.speaker_candidates) > 1
               for segment in segments
          ),
          overlap_count=overlap_count,
          style_removed_segment_count=sum(
               segment.style_removed
               for segment in segments
          ),
     )
