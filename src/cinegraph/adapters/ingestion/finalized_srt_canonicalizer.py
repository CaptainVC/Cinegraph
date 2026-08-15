

from uuid import UUID

from cinegraph.domain.enums.enum import Language, RightsStatus
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef
from cinegraph.ingestion.transcript_srt.models import TranscriptIngestionResult
from cinegraph.ingestion.transcript_srt.service import ingest_finalized_srt_text


class FinalizedSrtCanonicalizer:

    # Parse finalized labeled SRT text into canonical transcript segments and a report.
    def canonicalize(
            self,
            *,
            source_text: str,
            source_locator: str,
            source_version_id: UUID,
            episode: EpisodeRef,
            language: Language,
            rights_status: RightsStatus,
    ) -> TranscriptIngestionResult:
        return ingest_finalized_srt_text(
            source_text=source_text,
            source_path=source_locator,
            source_version_id=source_version_id,
            episode=episode,
            language=language,
            rights_status=rights_status,
        )
