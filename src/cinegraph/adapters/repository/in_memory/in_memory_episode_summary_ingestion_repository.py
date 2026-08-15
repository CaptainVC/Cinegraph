
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
    # Initialize in-memory source-document, version, active-version, and summary stores.
    def __init__(self):
        self._documents: dict[UUID, SourceDocument] = {}
        self._versions: dict[UUID, SourceVersion] = {}
        self._active_version: dict[UUID, UUID] = {}
        self._summaries_by_version: dict[UUID, EpisodeSummaryDocument] = {}

    # Return the active version only when its content hash matches the supplied hash.
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

    # Return the currently active version for a source document, if one exists.
    def get_active_version(
            self,
            source_document_id: UUID,
    ) -> SourceVersion | None:

        source_version_id = self._active_version.get(source_document_id)
        if source_version_id is None:
            return None

        return self._versions.get(source_version_id)

    # Validate and persist a new summary version while retiring the previous active version.
    def persist_new_episode_summary_ingestion(
            self,
            source_document: SourceDocument,
            source_version: SourceVersion,
            previous_active_version: SourceVersion | None,
            summary: EpisodeSummaryDocument,
    ) -> None:
        # Preserve the stable document record and reject metadata conflicts.
        stored_document = self._documents.get(source_document.source_document_id)
        if stored_document is None:
            self._documents[source_document.source_document_id] = source_document
        elif stored_document != source_document:
            raise ValueError(SourceErrorMessages.SOURCE_DOCUMENT_ID_METADATA_CONFLICT)

        # Reject writes based on an active version that changed since the caller read it.
        current_active_version = self.get_active_version(source_document.source_document_id)
        if current_active_version != previous_active_version:
            raise RuntimeError(SourceErrorMessages.ACTIVE_SOURCE_VERSION_CONFLICT)

        # Ensure the summary belongs to the version being persisted.
        if summary.source_version_id != source_version.source_version_id:
            raise ValueError(
                SourceErrorMessages.EPISODE_SUMMARY_SOURCE_VERSION_MISMATCH
            )

        # Retire the previous active version before activating the new one.
        if previous_active_version is not None:
            self._versions[previous_active_version.source_version_id] = replace(
                previous_active_version,
                status=SourceVersionStatus.RETIRED,
            )

        # Store the new version, active mapping, and summary.
        self._versions[source_version.source_version_id] = source_version
        self._active_version[source_document.source_document_id] = (
            source_version.source_version_id
        )
        self._summaries_by_version[source_version.source_version_id] = summary

    # Return a stored source version by identifier.
    def get_source_version(
        self,
        source_version_id: UUID
    ) -> SourceVersion | None:
        return self._versions.get(source_version_id)

    # Apply and persist review metadata for an existing source version.
    def update_source_version_review_status(
        self,
        source_version_id: UUID,
        review_status: SourceReviewStatus,
        reviewed_by: str,
        reviewed_at: datetime,
    ) -> SourceVersion:

        # Require the source version to exist before changing its review metadata.
        source_version = self._versions.get(source_version_id)
        if source_version is None:
            raise KeyError(
                SourceErrorMessages.SOURCE_VERSION_NOT_FOUND.format(
                    source_version_id=source_version_id,
                )
            )

        # Build the immutable source-version review transition.
        updated_source_version = replace(
            source_version,
            review_status=review_status,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
        )

        # Replace the stored version and return the updated value.
        self._versions[source_version_id] = updated_source_version
        return updated_source_version

    # Return the active summary only when its version is active and fully reviewed.
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
    # Expose all stored source versions in insertion order.
    def source_versions(self) -> tuple[SourceVersion, ...]:
        return tuple(self._versions.values())

    @property
    # Expose all stored episode summaries in insertion order.
    def summaries(self) -> tuple[EpisodeSummaryDocument, ...]:
        return tuple(self._summaries_by_version.values())
