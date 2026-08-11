
from dataclasses import dataclass, replace
from datetime import datetime
from cinegraph.domain.enums.enum import SourceReviewStatus

from uuid import UUID
from cinegraph.common.error_messages.source import SourceErrorMessages
from cinegraph.domain.enums.enum import SourceVersionStatus
from cinegraph.domain.models.episode_summary.episode_summary_document import EpisodeSummaryDocument
from cinegraph.domain.models.source.source_document import SourceDocument
from cinegraph.domain.models.source.review_status import (
    is_final_source_review_status,
)
from cinegraph.domain.models.source.source_version import SourceVersion

@dataclass(frozen=True, slots=True)
class EpisodeSummaryIngestion:
    source_document: SourceDocument
    source_version: SourceVersion
    summary: EpisodeSummaryDocument


class InMemoryEpisodeSummaryIngestionRepository:
    # Initializes the object with its required state.
    def __init__(self):
        self._documents: dict[UUID, SourceDocument] = {}
        self._versions: dict[UUID, SourceVersion] = {}
        self._active_version: dict[UUID, UUID] = {}
        self._summaries_by_version: dict[UUID, EpisodeSummaryDocument] = {}

    # Find the active version by content hash
    def find_active_version_by_content_hash(
            self,
            source_document_id: UUID,
            content_hash: str,
    ) -> SourceVersion | None:

        active_version = self.get_active_version(source_document_id)
        if active_version is None:
            return None

        if active_version.content_hash != content_hash:
            return None

        return active_version

    # Get the active version for a given source document ID
    def get_active_version(
            self,
            source_document_id: UUID,
    ) -> SourceVersion | None:

        source_version_id = self._active_version.get(source_document_id)
        if source_version_id is None:
            return None

        return self._versions.get(source_version_id)

    # Persist a new episode summary ingestion
    def persist_new_episode_summary_ingestion(
            self,
            source_document: SourceDocument,
            source_version: SourceVersion,
            previous_active_version: SourceVersion | None,
            summary: EpisodeSummaryDocument,
    ) -> None:
        # 1. Store or validate the stable source document.
        stored_document = self._documents.get(source_document.source_document_id)
        if stored_document is None:
            self._documents[source_document.source_document_id] = source_document
        elif stored_document != source_document:
            raise ValueError(SourceErrorMessages.SOURCE_DOCUMENT_ID_METADATA_CONFLICT)

        # 2. Detect concurrent active-version changes.
        current_active_version = self.get_active_version(source_document.source_document_id)
        if current_active_version != previous_active_version:
            raise RuntimeError(SourceErrorMessages.ACTIVE_SOURCE_VERSION_CONFLICT)

        # 3. Validate summary provenance.
        if summary.source_version_id != source_version.source_version_id:
            raise ValueError(
                SourceErrorMessages.EPISODE_SUMMARY_SOURCE_VERSION_MISMATCH
            )

        # 4. Retire the previous active version.
        if previous_active_version is not None:
            self._versions[previous_active_version.source_version_id] = replace(
                previous_active_version,
                status=SourceVersionStatus.RETIRED,
            )

        # 5. Persist the new active version and its summary.
        self._versions[source_version.source_version_id] = source_version
        self._active_version[source_document.source_document_id] = (
            source_version.source_version_id
        )
        self._summaries_by_version[source_version.source_version_id] = summary

    # Gets and returns the requested value.
    def get_source_version(
        self,
        source_version_id: UUID
    ) -> SourceVersion | None:
        return self._versions.get(source_version_id)

    # Updates the requested value in the repository.
    def update_source_version_review_status(
        self,
        source_version_id: UUID,
        review_status: SourceReviewStatus,
        reviewed_by: str,
        reviewed_at: datetime,
    ) -> SourceVersion:

        # 1. Load the source version to update.
        source_version = self._versions.get(source_version_id)
        if source_version is None:
            raise KeyError(
                SourceErrorMessages.SOURCE_VERSION_NOT_FOUND.format(
                    source_version_id=source_version_id,
                )
            )

        # 2. Create the immutable review-state transition.
        updated_source_version = replace(
            source_version,
            review_status=review_status,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
        )

        # 3. Replace the stored source version.
        self._versions[source_version_id] = updated_source_version
        return updated_source_version

    # Gets and returns the requested value.
    def get_active_reviewed_summary(
        self,
        source_document_id: UUID,
    ) -> EpisodeSummaryDocument | None:
        source_version = self.get_active_version(source_document_id)

        if source_version is None:
            return None

        if source_version.status is not SourceVersionStatus.ACTIVE:
            return None

        if not is_final_source_review_status(source_version.review_status):
            return None

        if source_version.review_status is not SourceReviewStatus.REVIEWED:
            return None

        return self._summaries_by_version.get(
            source_version.source_version_id
        )

    @property
    # Processes the supplied source versions values.
    def source_versions(self) -> tuple[SourceVersion, ...]:
        return tuple(self._versions.values())

    @property
    # Processes the supplied summaries values.
    def summaries(self) -> tuple[EpisodeSummaryDocument, ...]:
        return tuple(self._summaries_by_version.values())
