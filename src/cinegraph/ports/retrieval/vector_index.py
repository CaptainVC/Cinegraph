from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef
from cinegraph.domain.retrieval.retrieval_scope import RetrievalScope
from cinegraph.domain.retrieval.vector_data import QueryVector


@dataclass(frozen=True, slots=True)
class RetrievedSegment:
    segment_id: UUID
    episode: EpisodeRef
    start_ms: int
    end_ms: int
    text: str
    score: float


class VectorIndex(Protocol):
    # Search indexed transcript evidence using lexical, vector, and visibility constraints.
    def search_hybrid(
        self,
        query: QueryVector,
        scope: RetrievalScope,
        limit: int,
    ) -> tuple[RetrievedSegment, ...]: ...
