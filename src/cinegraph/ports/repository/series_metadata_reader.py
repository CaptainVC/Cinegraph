from typing import Protocol
from uuid import UUID

from cinegraph.domain.models.series_metadata import SeriesMetadataSnapshot


class SeriesMetadataReader(Protocol):
    def get_active_reviewed_series_metadata(
        self, source_document_id: UUID
    ) -> SeriesMetadataSnapshot | None: ...
