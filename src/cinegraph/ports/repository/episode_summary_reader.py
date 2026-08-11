from typing import Protocol
from uuid import UUID

from cinegraph.domain.models.episode_summary.episode_summary_document import (
    EpisodeSummaryDocument,
)


class EpisodeSummaryReader(Protocol):
    # Gets and returns the requested value.
    def get_active_reviewed_summary(
        self,
        source_document_id: UUID,
    ) -> EpisodeSummaryDocument | None: ...
