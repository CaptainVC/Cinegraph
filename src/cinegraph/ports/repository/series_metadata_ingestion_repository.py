from datetime import datetime
from typing import Protocol
from uuid import UUID

from cinegraph.domain.enums.enum import SourceReviewStatus
from cinegraph.domain.models.series_metadata import SeriesMetadataSnapshot
from cinegraph.domain.models.source.source_document import SourceDocument
from cinegraph.domain.models.source.source_version import SourceVersion


class SeriesMetadataIngestionRepository(Protocol):
    def find_active_version_by_content_hash(
        self, source_document_id: UUID, content_hash: str
    ) -> SourceVersion | None: ...
    def get_active_version(self, source_document_id: UUID) -> SourceVersion | None: ...
    def persist_new_series_metadata_ingestion(
        self,
        source_document: SourceDocument,
        source_version: SourceVersion,
        previous_active_version: SourceVersion | None,
        snapshot: SeriesMetadataSnapshot,
    ) -> None: ...
    def get_source_version(self, source_version_id: UUID) -> SourceVersion | None: ...
    def update_source_version_review_status(
        self,
        source_version_id: UUID,
        review_status: SourceReviewStatus,
        reviewed_by: str,
        reviewed_at: datetime,
    ) -> SourceVersion: ...
    def get_active_reviewed_series_metadata(
        self, source_document_id: UUID
    ) -> SeriesMetadataSnapshot | None: ...
