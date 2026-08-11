from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from cinegraph.domain.enums.enum import SourceReviewStatus, SourceVersionStatus
from cinegraph.domain.retrieval.vector_data import DocumentVector


@dataclass(frozen=True, slots=True)
class TranscriptIndexPayload:
    series_id: UUID
    season_id: UUID
    episode_id: UUID
    season_number: int
    episode_number: int
    start_ms: int
    end_ms: int
    text: str
    source_status: SourceVersionStatus
    review_status: SourceReviewStatus


@dataclass(frozen=True, slots=True)
class TranscriptIndexPoint:
    segment_id: UUID
    vector: DocumentVector
    payload: TranscriptIndexPayload


class TranscriptIndexWriter(Protocol):
    # Persist one complete batch of governed transcript index points.
    def upsert(self, points: tuple[TranscriptIndexPoint, ...]) -> None: ...