from cinegraph.application.models.index_transcript_segments import (
    IndexTranscriptSegmentsCommand,
)
from cinegraph.application.models.ingest_reviewed_corpus import (
    IngestReviewedCorpusCommand,
    IngestReviewedCorpusResult,
    IngestReviewedEpisodeOutcome,
)
from cinegraph.application.models.ingest_reviewed_subtitle import (
    IngestReviewedSubtitleCommand,
)
from cinegraph.application.service.index_transcript_segments_service import (
    IndexTranscriptSegmentsService,
)
from cinegraph.application.service.ingest_reviewed_subtitle_service import (
    IngestReviewedSubtitleService,
)
from cinegraph.common.error_messages import CorpusIngestionErrorMessages
from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.domain.enums.enum import (
    Language,
    RightsStatus,
    SourceAcquisitionMethod,
    SourceKind,
)
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.source import SourceDocument


class IngestReviewedCorpusService:
    def __init__(
        self,
        subtitle_ingestion: IngestReviewedSubtitleService,
        transcript_indexing: IndexTranscriptSegmentsService,
    ) -> None:
        self._subtitle_ingestion = subtitle_ingestion
        self._transcript_indexing = transcript_indexing

    # Ingest and index each ledger-approved episode using deterministic identities.
    def execute(
        self,
        command: IngestReviewedCorpusCommand,
    ) -> IngestReviewedCorpusResult:
        outcomes = []
        for item in command.batch.items:
            origin = f"reviewed-subtitle:{item.source_path.name}"
            source_document = SourceDocument(
                source_document_id=IdentifierGenerator.transcript_source_document_id(
                    item.episode.episode_id,
                    Language.ENGLISH,
                    origin,
                ),
                title=item.episode_title,
                kind=SourceKind.SUBTITLE,
                origin=origin,
            )
            ingestion = self._subtitle_ingestion.execute(
                IngestReviewedSubtitleCommand(
                    source_document=source_document,
                    source_locator=str(item.source_path),
                    episode=item.episode,
                    language=Language.ENGLISH,
                    rights_status=RightsStatus.ALLOWED,
                    acquisition_method=SourceAcquisitionMethod.LOCAL_FILESYSTEM,
                    reviewed_by=item.reviewed_by,
                    reviewed_at=item.reviewed_at,
                    review_status=item.review_status,
                )
            )
            if ingestion.source_version.content_hash != item.content_sha256:
                raise InvalidModelError(
                    CorpusIngestionErrorMessages.INGESTED_SOURCE_HASH_MUST_MATCH_REVIEW_LEDGER
                )
            indexed_count = 0
            if not ingestion.was_already_ingested:
                indexed_count = self._transcript_indexing.execute(
                    IndexTranscriptSegmentsCommand(
                        source_version=ingestion.source_version,
                        segments=ingestion.segments,
                    )
                ).indexed_segment_count
            outcomes.append(
                IngestReviewedEpisodeOutcome(
                    episode=item.episode,
                    source_version_id=ingestion.source_version.source_version_id,
                    segment_count=len(ingestion.segments),
                    indexed_segment_count=indexed_count,
                    was_already_ingested=ingestion.was_already_ingested,
                )
            )
        return IngestReviewedCorpusResult(outcomes=tuple(outcomes))
