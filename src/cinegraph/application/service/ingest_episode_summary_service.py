from hashlib import sha256

from cinegraph.common.identifiers.generator import IdentifierGenerator
from cinegraph.domain.enums.enum import SourceAcquisitionMethod, SourceReviewStatus, SourceVersionStatus
from cinegraph.domain.models.episode_summary.episode_summary_document import EpisodeSummaryDocument
from cinegraph.domain.models.source.source_version import SourceVersion
from cinegraph.ingestion.episode_summary.ingest_episode_summary import IngestEpisodeSummaryCommand, IngestEpisodeSummaryResult
from cinegraph.ports.episode_summary.episode_summary_provider import EpisodeSummaryProvider
from cinegraph.ports.repository.episode_summary_ingestion_repository import EpisodeSummaryIngestionRepository


class IngestEpisodeSummaryService:

    def __init__(
            self,
            provider: EpisodeSummaryProvider,
            repository: EpisodeSummaryIngestionRepository,
    ) -> None:
        self._provider = provider
        self._repository = repository

    def execute(
            self,
            command: IngestEpisodeSummaryCommand
    ) -> IngestEpisodeSummaryResult:

        # 1. Fetch the episode summary from the provider
        fetched = self._provider.fetch(
            page_title=command.page_title,
            language=command.language
        )

        # 2. Check if the fetched content has already been ingested by comparing content hashes
        content_hash = sha256(fetched.text.encode("utf-8")).hexdigest()
        existing_version = self._repository.find_active_version_by_content_hash(
            command.source_document.source_document_id,
            content_hash
        )
        if existing_version is not None:
            return IngestEpisodeSummaryResult(
                source_version=existing_version,
                summary=None,
                was_already_ingested=True
            )

        # 3. Create a new source version
        previous_active_version = self._repository.get_active_version(
            command.source_document.source_document_id
        )
        source_version = SourceVersion(
            source_version_id=IdentifierGenerator.source_version_id(
                command.source_document.source_document_id,
                content_hash
            ),
            source_document_id=command.source_document.source_document_id,
            content_hash=content_hash,
            rights_status=command.rights_status,
            acquisition_method=SourceAcquisitionMethod.MEDIAWIKI_API,
            review_status=SourceReviewStatus.PENDING,
            status=SourceVersionStatus.ACTIVE,
            acquired_at=fetched.retrieved_at,
            parent_source_version_id=(
                previous_active_version.source_version_id
                if previous_active_version is not None
                else None
            )
        )

        # 4. Create the episode summary document
        summary = EpisodeSummaryDocument(
            summary_id=IdentifierGenerator.episode_summary_document_id(
                source_version.source_version_id,
                command.episode.episode_id,
                fetched.language,
            ),
            source_version_id=source_version.source_version_id,
            episode=command.episode,
            text=fetched.text,
            language=fetched.language,
            rights_status=command.rights_status,
            canonical_url=fetched.canonical_url,
            revision_id=fetched.revision_id,
            revision_timestamp=fetched.revision_timestamp,
            attribution=fetched.attribution
        )

        # 5. Persist the new episode summary ingestion
        self._repository.persist_new_episode_summary_ingestion(
            source_document=command.source_document,
            source_version=source_version,
            previous_active_version=previous_active_version,
            summary=summary
        )

        # 6. Return the result
        return IngestEpisodeSummaryResult(
            source_version=source_version,
            summary=summary,
            was_already_ingested=False
        )