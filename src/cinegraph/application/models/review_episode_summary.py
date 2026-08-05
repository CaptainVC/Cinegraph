

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from cinegraph.domain.enums.enum import SourceReviewStatus
from cinegraph.domain.models.source.source_version import SourceVersion

@dataclass(frozen=True, slots=True)
class ReviewEpisodeSummaryCommand:
    source_version_id: UUID
    review_status: SourceReviewStatus
    reviewed_by: str
    reviewed_at: datetime

@dataclass(frozen=True, slots=True)
class ReviewEpisodeSummaryResult:
    source_version: SourceVersion
    was_already_reviewed: bool