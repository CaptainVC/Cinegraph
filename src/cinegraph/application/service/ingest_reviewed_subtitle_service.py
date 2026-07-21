from hashlib import sha256

from cinegraph.application.models.ingest_reviewed_subtitle import (
    IngestReviewedSubtitleCommand,
    IngestReviewedSubtitleResult,
)
from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.domain.enums.enum import SourceReviewStatus, SourceVersionStatus
from cinegraph.domain.models.source.source_version import SourceVersion
from cinegraph.ports.date_time.clock import Clock
from cinegraph.ports.repository.transcript_ingestion_repository import TranscriptIngestionRepository
from cinegraph.ports.subtitle_processing.finalized_subtitle_canonicalizer import (
    FinalizedSubtitleCanonicalizer,
)
from cinegraph.ports.subtitle_processing.subtitle_text_reader import SubtitleTextReader


class IngestReviewedSubtitleService:

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


    def execute(
            self,
            command: IngestReviewedSubtitleCommand
    ) -> IngestReviewedSubtitleResult:

        # 1. Load the subtitle text from the provided source locator
        source_text = self._subtitle_text_reader.read_text(
            command.source_locator
        )

        # 2. Generate a content hash for the subtitle text to check for duplicates
        content_hash = sha256(source_text.encode("utf-8")).hexdigest()

        # 3. Check if a source version with the same content hash already exists
        existing_version = self._repository.find_active_version_by_content_hash(
            source_document_id=command.source_document.source_document_id,
            content_hash=content_hash,
        )

        # 4. If an existing version is found, return it without re-ingesting
        if existing_version is not None:
            return IngestReviewedSubtitleResult(
                source_version=existing_version,
                segments=(),
                report=None,
                was_already_ingested=True,
            )

        # 5. If no existing version is found, create a new source version and persist it
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
            review_status=SourceReviewStatus.REVIEWED,
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

        # 6. Canonicalize the subtitle text to produce segments and a report
        # Canonicalization here means transforming the raw subtitle text into a structured format
        # that can be stored and queried efficiently, while also generating a report that
        # summarizes the canonicalization process.
        canonical_result = self._canonicalizer.canonicalize(
            source_text=source_text,
            source_locator=command.source_locator,
            source_version_id=source_version.source_version_id,
            episode=command.episode,
            language=command.language,
            rights_status=command.rights_status,
        )

        # 7. Persist the new source version and its segments in the repository
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