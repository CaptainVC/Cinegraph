import math
from dataclasses import dataclass
from numbers import Real
from typing import Protocol
from uuid import UUID

from cinegraph.common.error_messages import RetrievalErrorMessages
from cinegraph.config.transcript_chunking import TRANSCRIPT_INDEX_REVISION
from cinegraph.domain.enums.enum import Language, RightsStatus
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef
from cinegraph.domain.retrieval.retrieval_scope import RetrievalScope
from cinegraph.domain.retrieval.vector_data import QueryVector


@dataclass(frozen=True, slots=True)
class RetrievedSegment:
    segment_id: UUID
    source_version_id: UUID
    episode: EpisodeRef
    start_ms: int
    end_ms: int
    text: str
    language: Language
    rights_status: RightsStatus
    score: float
    member_segment_ids: tuple[UUID, ...]
    index_revision: str
    ordinal: int

    # Protect the application boundary from malformed retrieval implementations.
    def __post_init__(self) -> None:
        if not isinstance(self.segment_id, UUID) or not isinstance(self.source_version_id, UUID):
            raise InvalidModelError(RetrievalErrorMessages.RETRIEVED_SEGMENT_IDS_MUST_BE_UUIDS)
        if not isinstance(self.episode, EpisodeRef):
            raise InvalidModelError(RetrievalErrorMessages.RETRIEVED_SEGMENT_EPISODE_MUST_BE_VALID)
        if (
            isinstance(self.start_ms, bool)
            or not isinstance(self.start_ms, int)
            or isinstance(self.end_ms, bool)
            or not isinstance(self.end_ms, int)
            or self.start_ms < 0
            or self.end_ms <= self.start_ms
        ):
            raise InvalidModelError(RetrievalErrorMessages.RETRIEVED_SEGMENT_TIMING_MUST_BE_VALID)
        if not isinstance(self.text, str) or not self.text or self.text.strip() != self.text:
            raise InvalidModelError(RetrievalErrorMessages.RETRIEVED_SEGMENT_TEXT_MUST_BE_VALID)
        if not isinstance(self.language, Language) or not isinstance(
            self.rights_status, RightsStatus
        ):
            raise InvalidModelError(
                RetrievalErrorMessages.RETRIEVED_SEGMENT_GOVERNANCE_MUST_BE_VALID
            )
        if (
            isinstance(self.score, bool)
            or not isinstance(self.score, Real)
            or not math.isfinite(self.score)
        ):
            raise InvalidModelError(RetrievalErrorMessages.RETRIEVED_SEGMENT_SCORE_MUST_BE_FINITE)
        if self.rights_status is not RightsStatus.ALLOWED:
            raise InvalidModelError(
                RetrievalErrorMessages.RETRIEVED_SEGMENT_GOVERNANCE_MUST_BE_VALID
            )
        if (
            not isinstance(self.member_segment_ids, tuple)
            or not self.member_segment_ids
            or any(not isinstance(item, UUID) for item in self.member_segment_ids)
            or len(set(self.member_segment_ids)) != len(self.member_segment_ids)
        ):
            raise InvalidModelError(
                RetrievalErrorMessages.QDRANT_RESULT_MEMBER_SEGMENTS_MUST_BE_VALID
            )
        if (
            isinstance(self.ordinal, bool)
            or not isinstance(self.ordinal, int)
            or self.ordinal < 0
            or self.index_revision != TRANSCRIPT_INDEX_REVISION
        ):
            raise InvalidModelError(RetrievalErrorMessages.QDRANT_RESULT_INDEX_REVISION_MUST_MATCH)


class VectorIndex(Protocol):
    # Search indexed transcript evidence using lexical, vector, and visibility constraints.
    def search_hybrid(
        self,
        query: QueryVector,
        scope: RetrievalScope,
        limit: int,
    ) -> tuple[RetrievedSegment, ...]: ...

    # Resolve bounded, already-authorized transcript chunks by stable IDs.
    def retrieve_by_ids(
        self,
        segment_ids: tuple[UUID, ...],
        scope: RetrievalScope,
    ) -> tuple[RetrievedSegment, ...]: ...
