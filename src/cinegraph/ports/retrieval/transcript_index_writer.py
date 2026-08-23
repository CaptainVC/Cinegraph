from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from cinegraph.domain.enums.enum import (
    Language,
    RightsStatus,
    SourceReviewStatus,
    SourceVersionStatus,
)
from cinegraph.domain.retrieval.vector_data import DocumentVector


@dataclass(frozen=True, slots=True)
class TranscriptIndexPayload:
    source_version_id: UUID
    series_id: UUID
    season_id: UUID
    episode_id: UUID
    season_number: int
    episode_number: int
    start_ms: int
    end_ms: int
    text: str
    language: Language
    rights_status: RightsStatus
    source_status: SourceVersionStatus
    review_status: SourceReviewStatus
    member_segment_ids: tuple[UUID, ...]
    chunk_ordinal: int
    index_revision: str


@dataclass(frozen=True, slots=True)
class TranscriptIndexPoint:
    chunk_id: UUID
    vector: DocumentVector
    payload: TranscriptIndexPayload


class TranscriptIndexWriter(Protocol):
    # Replace one source version safely, writing new points before retiring the parent.
    def replace_source_version(
        self,
        new_source_version_id: UUID,
        retired_source_version_id: UUID | None,
        points: tuple[TranscriptIndexPoint, ...],
    ) -> None: ...
