from dataclasses import dataclass
from uuid import UUID

from cinegraph.domain.models.access import CorpusAccessScope
from cinegraph.domain.models.watch_state import EpisodeRef, ProfileWatchState
from cinegraph.ports.retrieval import RetrievedSegment


@dataclass(frozen=True, slots=True)
class SearchVisibleHybridSegmentsQuery:
    query: str
    series_id: UUID
    candidate_episodes: tuple[EpisodeRef, ...]
    profile_watch_state: ProfileWatchState | None
    corpus_access_scope: CorpusAccessScope
    limit: int = 10


@dataclass(frozen=True, slots=True)
class SearchVisibleHybridSegmentsResult:
    matches: tuple[RetrievedSegment, ...]
    visible_episode_count: int
