from hashlib import sha256

from cinegraph.application.models.ingest_reviewed_subtitle import (
    IngestReviewedSubtitleCommand,
    IngestReviewedSubtitleResult,
)
from cinegraph.common.error_messages import TranscriptErrorMessages
from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.domain.enums.enum import SourceVersionStatus
from cinegraph.domain.models.source.review_status import is_source_version_approved
from cinegraph.domain.models.source.source_version import SourceVersion
from cinegraph.ports.date_time.clock import Clock
from cinegraph.ports.repository.transcript_ingestion_repository import TranscriptIngestionRepository
from cinegraph.ports.subtitle_processing.finalized_subtitle_canonicalizer import (
    FinalizedSubtitleCanonicalizer,
)
from cinegraph.ports.subtitle_processing.subtitle_text_reader import SubtitleTextReader


class IngestReviewedSubtitleService:

    # Store the repository, subtitle reader, canonicalizer, and clock dependencies.
    def __init__(
            self,
            repository: TranscriptIngestionRepository,
            subtitle_text_reader: SubtitleTextReader,
            canonicalizer: FinalizedSubtitleCanonicalizer,
            clock: Clock,
    ) -> None:
        self._repository = repository
        self._subtitle_text_reader = subtitle_text_reader
        self._canonicalizer = canonicalizer
        self._clock = clock


    # Read, deduplicate, canonicalize, and persist a reviewed subtitle ingestion.
    def execute(
            self,
            command: IngestReviewedSubtitleCommand
    ) -> IngestReviewedSubtitleResult:

        if not is_source_version_approved(command.review_status):
            raise ValueError(
                TranscriptErrorMessages.SUBTITLE_INGESTION_REQUIRES_APPROVED_REVIEW_STATUS
            )

        # Read subtitle text from the caller-provided source locator.
        source_text = self._subtitle_text_reader.read_text(
            command.source_locator
        )

        # Hash the source text so identical active content is idempotent.
        content_hash = sha256(source_text.encode("utf-8")).hexdigest()

        # Look for an active version with the same content hash.
        existing_version = self._repository.find_active_version_by_content_hash(
            source_document_id=command.source_document.source_document_id,
            content_hash=content_hash,
        )

        # Return the existing version without canonicalizing duplicate content.
        if existing_version is not None:
            return IngestReviewedSubtitleResult(
                source_version=existing_version,
                segments=(),
                report=None,
                was_already_ingested=True,
            )

        # Create a reviewed active version linked to the previous active version.
        previous_active_version = self._repository.get_active_version(
            source_document_id=command.source_document.source_document_id
        )

        source_version = SourceVersion(
            source_version_id=IdentifierGenerator.source_version_id(
                source_document_id=command.source_document.source_document_id,
                content_hash=content_hash
            ),
            source_document_id=command.source_document.source_document_id,
            content_hash=content_hash,
            rights_status=command.rights_status,
            acquisition_method=command.acquisition_method,
            review_status=command.review_status,
            status=SourceVersionStatus.ACTIVE,
            acquired_at=self._clock.now_utc(),
            parent_source_version_id=(
                previous_active_version.source_version_id
                if previous_active_version is not None
                else None
            ),
            reviewed_by=command.reviewed_by,
            reviewed_at=command.reviewed_at,
        )

        # Convert labeled subtitle text into canonical transcript segments and a report.
        canonical_result = self._canonicalizer.canonicalize(
            source_text=source_text,
            source_locator=command.source_locator,
            source_version_id=source_version.source_version_id,
            episode=command.episode,
            language=command.language,
            rights_status=command.rights_status,
        )

        # Persist the new version and its canonical segments.
        self._repository.persist_new_subtitle_ingestion(
            source_document=command.source_document,
            source_version=source_version,
            previous_active_version=previous_active_version,
            segments=canonical_result.segments,
        )

        # 8. Return the result of the ingestion process
        return IngestReviewedSubtitleResult(
            source_version=source_version,
            segments=canonical_result.segments,
            report=canonical_result.report,
            was_already_ingested=False,
        )
