from dataclasses import replace
from datetime import datetime
from uuid import UUID

from cinegraph.common.error_messages.source import SourceErrorMessages
from cinegraph.domain.enums.enum import SourceReviewStatus, SourceVersionStatus
from cinegraph.domain.models.series_metadata import SeriesMetadataSnapshot
from cinegraph.domain.models.source.review_status import (
    is_final_source_review_status,
    is_source_version_approved,
)
from cinegraph.domain.models.source.source_document import SourceDocument
from cinegraph.domain.models.source.source_version import SourceVersion


class InMemorySeriesMetadataIngestionRepository:
    def __init__(self) -> None:
        self._documents: dict[UUID, SourceDocument] = {}
        self._versions: dict[UUID, SourceVersion] = {}
        self._active: dict[UUID, UUID] = {}
        self._snapshots: dict[UUID, SeriesMetadataSnapshot] = {}

    def get_active_version(self, source_document_id: UUID) -> SourceVersion | None:
        version_id = self._active.get(source_document_id)
        return self._versions.get(version_id) if version_id else None

    def find_active_version_by_content_hash(
        self, source_document_id: UUID, content_hash: str
    ) -> SourceVersion | None:
        version = self.get_active_version(source_document_id)
        return version if version and version.content_hash == content_hash else None

    def persist_new_series_metadata_ingestion(
        self,
        source_document: SourceDocument,
        source_version: SourceVersion,
        previous_active_version: SourceVersion | None,
        snapshot: SeriesMetadataSnapshot,
    ) -> None:
        stored = self._documents.get(source_document.source_document_id)
        if stored is not None and stored != source_document:
            raise ValueError(SourceErrorMessages.SOURCE_DOCUMENT_ID_METADATA_CONFLICT)
        current = self.get_active_version(source_document.source_document_id)
        if current != previous_active_version:
            raise RuntimeError(SourceErrorMessages.ACTIVE_SOURCE_VERSION_CONFLICT)
        if snapshot.source_version_id != source_version.source_version_id:
            raise ValueError(
                SourceErrorMessages.SERIES_METADATA_SOURCE_VERSION_MISMATCH
            )
        self._documents.setdefault(source_document.source_document_id, source_document)
        if previous_active_version:
            self._versions[previous_active_version.source_version_id] = replace(
                previous_active_version, status=SourceVersionStatus.RETIRED
            )
        self._versions[source_version.source_version_id] = source_version
        self._active[source_document.source_document_id] = (
            source_version.source_version_id
        )
        self._snapshots[source_version.source_version_id] = snapshot

    def get_source_version(self, source_version_id: UUID) -> SourceVersion | None:
        return self._versions.get(source_version_id)

    def update_source_version_review_status(
        self,
        source_version_id: UUID,
        review_status: SourceReviewStatus,
        reviewed_by: str,
        reviewed_at: datetime,
    ) -> SourceVersion:
        current = self._versions.get(source_version_id)
        if current is None:
            raise KeyError(
                SourceErrorMessages.SOURCE_VERSION_NOT_FOUND.format(
                    source_version_id=source_version_id
                )
            )
        if not is_final_source_review_status(review_status):
            raise ValueError(
                SourceErrorMessages.SOURCE_VERSION_REVIEW_REQUIRES_FINAL_DECISION
            )
        updated = replace(
            current,
            review_status=review_status,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
        )
        self._versions[source_version_id] = updated
        return updated

    def get_active_reviewed_series_metadata(
        self, source_document_id: UUID
    ) -> SeriesMetadataSnapshot | None:
        version = self.get_active_version(source_document_id)
        if (
            version is None
            or version.status is not SourceVersionStatus.ACTIVE
            or not is_source_version_approved(version.review_status)
        ):
            return None
        return self._snapshots.get(version.source_version_id)

    @property
    def source_versions(self) -> tuple[SourceVersion, ...]:
        return tuple(self._versions.values())

    @property
    def snapshots(self) -> tuple[SeriesMetadataSnapshot, ...]:
        return tuple(self._snapshots.values())
