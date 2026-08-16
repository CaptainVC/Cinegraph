from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from cinegraph.domain.enums.enum import SourceReviewStatus
from cinegraph.domain.models.watch_state import EpisodeRef


@dataclass(frozen=True, slots=True)
class ReviewedSubtitleBatchItem:
    episode: EpisodeRef
    episode_title: str
    source_path: Path
    content_sha256: str
    reviewed_by: str
    reviewed_at: datetime
    review_status: SourceReviewStatus


@dataclass(frozen=True, slots=True)
class ReviewedSubtitleBatch:
    items: tuple[ReviewedSubtitleBatchItem, ...]


@dataclass(frozen=True, slots=True)
class IngestReviewedCorpusCommand:
    batch: ReviewedSubtitleBatch


@dataclass(frozen=True, slots=True)
class IngestReviewedEpisodeOutcome:
    episode: EpisodeRef
    source_version_id: UUID
    segment_count: int
    indexed_segment_count: int
    was_already_ingested: bool


@dataclass(frozen=True, slots=True)
class IngestReviewedCorpusResult:
    outcomes: tuple[IngestReviewedEpisodeOutcome, ...]

    @property
    def indexed_segment_count(self) -> int:
        return sum(item.indexed_segment_count for item in self.outcomes)
