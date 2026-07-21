
from typing import List, Optional

from uuid import UUID
from cinegraph.domain.models.episode_summary.episode_summary_document import EpisodeSummaryDocument
from cinegraph.domain.models.source.source_document import SourceDocument
from cinegraph.domain.models.source.source_version import SourceVersion


class InMemoryEpisodeSummaryIngestionRepository:
    def __init__(self):
        self._ingestions: List[EpisodeSummaryIngestion] = []

    def find_active_version_by_content_hash(
            self,
            source_document_id: UUID,
            content_hash: str,
    ) -> SourceVersion | None:
        for ingestion in self._ingestions:
            if (
                ingestion.source_document.source_document_id == source_document_id
                and ingestion.source_version.content_hash == content_hash
            ):
                return ingestion.source_version
        return None

    def get_active_version(
            self,
            source_document_id: UUID,
    ) -> SourceVersion | None:
        for ingestion in self._ingestions:
            if ingestion.source_document.source_document_id == source_document_id:
                return ingestion.source_version
        return None

    def persist_new_episode_summary_ingestion(
            self,
            source_document: SourceDocument,
            source_version: SourceVersion,
            previous_active_version: SourceVersion | None,
            summary: EpisodeSummaryDocument,
    ) -> None:
        ingestion = EpisodeSummaryIngestion(
            source_document=source_document,
            source_version=source_version,
            previous_active_version=previous_active_version,
            summary=summary
        )
        self._ingestions.append(ingestion)