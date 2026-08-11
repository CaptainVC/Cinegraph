from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef
from cinegraph.domain.retrieval.retrieval_scope import RetrievalScope


@dataclass(frozen=True, slots=True)
class RetrievedSegment:
    segment_id: UUID
    episode: EpisodeRef
    start_ms: int
    end_ms: int
    text: str
    score: float


class VectorIndex(Protocol):
    def search_hybrid(
        self,
        query: str,
        scope: RetrievalScope,
        limit: int,
    ) -> tuple[RetrievedSegment, ...]: ...
