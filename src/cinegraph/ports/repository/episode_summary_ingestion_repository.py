from datetime import datetime
from typing import Protocol
from uuid import UUID

from cinegraph.domain.enums.enum import SourceReviewStatus
from cinegraph.domain.models.episode_summary.episode_summary_document import EpisodeSummaryDocument
from cinegraph.domain.models.source.source_document import SourceDocument
from cinegraph.domain.models.source.source_version import SourceVersion


class EpisodeSummaryIngestionRepository(Protocol):

    def find_active_version_by_content_hash(
            self,
            source_document_id: UUID,
            content_hash: str,
    ) -> SourceVersion | None: ...

    def get_active_version(
            self,
            source_document_id: UUID,
    ) -> SourceVersion | None: ...

    def persist_new_episode_summary_ingestion(
            self,
            source_document: SourceDocument,
            source_version: SourceVersion,
            previous_active_version: SourceVersion | None,
            summary: EpisodeSummaryDocument,
    ) -> None: ...

    def get_source_version(
            self,
            source_version_id: UUID
    ) -> SourceVersion | None: ...

    def update_source_version_review_status(
            self,
            source_version_id: UUID,
            review_status: SourceReviewStatus,
            reviewed_by: str,
            reviewed_at: datetime,
    ) -> SourceVersion: ...
