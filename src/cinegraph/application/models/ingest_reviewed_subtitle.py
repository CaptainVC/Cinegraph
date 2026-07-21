from dataclasses import dataclass
from datetime import datetime


from cinegraph.domain.enums.enum import (
    Language,
    RightsStatus,
    SourceAcquisitionMethod,
)
from cinegraph.domain.models.source.source_document import SourceDocument
from cinegraph.domain.models.source.source_version import SourceVersion
from cinegraph.domain.models.transcript.transcript_segment import TranscriptSegment
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef
from cinegraph.ingestion.transcript_srt.models import TranscriptIngestionReport

@dataclass(frozen=True, slots=True)
class IngestReviewedSubtitleCommand:
    source_document: SourceDocument
    source_locator: str
    episode: EpisodeRef
    language: Language
    rights_status: RightsStatus
    acquisition_method: SourceAcquisitionMethod
    reviewed_by: str
    reviewed_at: datetime

@dataclass(frozen=True, slots=True)
class IngestReviewedSubtitleResult:
    source_version: SourceVersion
    segments: tuple[TranscriptSegment, ...]
    report: TranscriptIngestionReport | None
    was_already_ingested: bool
