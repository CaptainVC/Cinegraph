

from typing import Protocol
from uuid import UUID

from cinegraph.domain.enums.enum import Language, RightsStatus
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef
from cinegraph.ingestion.transcript_srt.models import TranscriptIngestionResult


class FinalizedSubtitleCanonicalizer(Protocol):
    # Convert finalized labeled subtitle text into canonical transcript segments and a report.
    def canonicalize(
            self,
            source_text: str,
            source_locator: str,
            source_version_id: UUID,
            episode: EpisodeRef,
            language: Language,
            rights_status: RightsStatus,
    ) -> TranscriptIngestionResult:
        ...
