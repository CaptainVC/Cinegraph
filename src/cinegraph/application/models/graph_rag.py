from dataclasses import dataclass
from uuid import UUID

from cinegraph.common.error_messages import GraphRagErrorMessages
from cinegraph.config.graph_rag import (
    DEFAULT_GRAPH_RAG_CLAIMS,
    DEFAULT_GRAPH_RAG_EVIDENCE_PER_CLAIM,
    DEFAULT_GRAPH_RAG_HOPS,
    MAX_GRAPH_RAG_CLAIMS,
)
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.access import CorpusAccessScope
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef
from cinegraph.domain.models.watch_state.profile_watch_state import ProfileWatchState
from cinegraph.ports.graph.graph_claim_read_models import GraphRagReadClaim


@dataclass(frozen=True, slots=True)
class GraphRagQuery:
    series_id: UUID
    seed_terms: tuple[str, ...]
    candidate_episodes: tuple[EpisodeRef, ...]
    corpus_access_scope: CorpusAccessScope
    profile_watch_state: ProfileWatchState | None = None
    predicates: tuple[str, ...] = ()
    hops: int = DEFAULT_GRAPH_RAG_HOPS
    claim_limit: int = DEFAULT_GRAPH_RAG_CLAIMS
    evidence_per_claim: int = DEFAULT_GRAPH_RAG_EVIDENCE_PER_CLAIM

    def __post_init__(self) -> None:
        if not isinstance(self.series_id, UUID):
            raise InvalidModelError(GraphRagErrorMessages.QUERY_SERIES_INVALID)
        if not isinstance(self.corpus_access_scope, CorpusAccessScope):
            raise InvalidModelError(GraphRagErrorMessages.QUERY_INVALID)
        if self.profile_watch_state is not None and not isinstance(
            self.profile_watch_state, ProfileWatchState
        ):
            raise InvalidModelError(GraphRagErrorMessages.QUERY_INVALID)
        if not isinstance(self.seed_terms, tuple) or not self.seed_terms:
            raise InvalidModelError(GraphRagErrorMessages.QUERY_SEEDS_INVALID)
        if not isinstance(self.predicates, tuple):
            raise InvalidModelError(GraphRagErrorMessages.QUERY_PREDICATES_INVALID)
        if not isinstance(self.candidate_episodes, tuple) or any(
            not isinstance(item, EpisodeRef) for item in self.candidate_episodes
        ):
            raise InvalidModelError(GraphRagErrorMessages.QUERY_EPISODES_INVALID)
        for value in (self.hops, self.claim_limit, self.evidence_per_claim):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise InvalidModelError(GraphRagErrorMessages.QUERY_LIMIT_INVALID)


@dataclass(frozen=True, slots=True)
class GraphRagResult:
    claims: tuple[GraphRagReadClaim, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.claims, tuple)
            or any(not isinstance(item, GraphRagReadClaim) for item in self.claims)
            or len(self.claims) > MAX_GRAPH_RAG_CLAIMS
        ):
            raise InvalidModelError(GraphRagErrorMessages.RESULT_INVALID)
        ids = tuple(item.claim_id for item in self.claims)
        if len(set(ids)) != len(ids):
            raise InvalidModelError(GraphRagErrorMessages.RESULT_INVALID)
