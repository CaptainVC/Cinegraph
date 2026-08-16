
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


# Read, parse, canonicalize, and report a finalized SRT file.
def ingest_finalized_srt(
     *,
     source_path: Path,
     source_version_id: UUID,
     episode: EpisodeRef,
     language: Language,
     rights_status: RightsStatus,
) -> TranscriptIngestionResult:
     # Read the source file and parse its validated SRT cues.
    cues = parse_srt(read_srt_text(source_path))

     # Convert each labeled cue into a canonical transcript segment.
    mapped_segments = tuple(
        _to_transcript_segment(
            cue=cue,
            source_version_id=source_version_id,
            episode=episode,
            language=language,
            rights_status=rights_status,
        )
        for cue in cues
    )
    segments = tuple(segment for segment in mapped_segments if segment is not None)

    return TranscriptIngestionResult(
          segments=segments,
          report=_build_report(
               source_path=source_path,
               cues=cues,
               segments=segments,
          ),
    )

# Canonicalize supplied finalized SRT text while retaining its source path in the report.
def ingest_finalized_srt_text(
     *,
     source_text: str,
     source_path: Path,
     source_version_id: UUID,
     episode: EpisodeRef,
     language: Language,
     rights_status: RightsStatus,
) -> TranscriptIngestionResult:
     # Parse caller-supplied SRT text into validated cues.
    cues = parse_srt(source_text)

     # Convert the cues into transcript segments and summarize the ingestion.
    mapped_segments = tuple(
        _to_transcript_segment(
            cue=cue,
            source_version_id=source_version_id,
            episode=episode,
            language=language,
            rights_status=rights_status,
        )
        for cue in cues
    )
    segments = tuple(segment for segment in mapped_segments if segment is not None)

    return TranscriptIngestionResult(
          segments=segments,
          report=_build_report(
               source_path=source_path,
               cues=cues,
               segments=segments,
          ),
    )


# Validate speaker labels and build one canonical transcript segment from an SRT cue.
def _to_transcript_segment(
     *,
     cue: ParsedSrtCue,
     source_version_id: UUID,
     episode: EpisodeRef,
     language: Language,
     rights_status: RightsStatus,
) -> TranscriptSegment | None:
     # Require verified labels, normalize dialogue, and deduplicate speakers per cue.
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
          style_removed = style_removed or removed_styles
          if not dialogue:
               continue

          dialogue_parts.append(dialogue)

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

     if not dialogue_parts:
          return None

     # Join normalized dialogue and derive stable segment and speaker identifiers.
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


# Remove style tags, collapse whitespace, and report whether styling was removed.
def _canonicalize_dialogue(value: str) -> tuple[str, bool]:
     without_styles = SrtPatterns.STYLE_TAG_PATTERN.sub(" ", value)
     text = SrtPatterns.WHITESPACE_PATTERN.sub(" ", without_styles).strip()
     return text, without_styles != value


# Normalize a verified speaker label to trimmed uppercase text.
def _normalize_speaker_name(value: str) -> str:
     return SrtPatterns.WHITESPACE_PATTERN.sub(" ", value).strip().upper()


# Derive the stable speaker identifier for a series and normalized speaker name.
def _speaker_id(
     *,
     series_id: UUID,
     speaker_name: str,
) -> UUID:
    return IdentifierGenerator.speaker_id(series_id, speaker_name)


# Derive the stable identifier for one transcript cue and its canonical text.
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


# Summarize cue, speaker, overlap, and style-removal counts for the ingestion.
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
          skipped_non_dialogue_cue_count=len(cues) - len(segments),
     )
