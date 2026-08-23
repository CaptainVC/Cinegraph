from dataclasses import dataclass

from cinegraph.common.error_messages import GraphRagErrorMessages

GRAPH_RAG_QUERY_REVISION = "graph-rag-v1"
MAX_GRAPH_RAG_SEEDS = 8
MAX_GRAPH_RAG_PREDICATES = 8
DEFAULT_GRAPH_RAG_HOPS = 2
MAX_GRAPH_RAG_HOPS = 2
DEFAULT_GRAPH_RAG_CLAIMS = 25
MAX_GRAPH_RAG_CLAIMS = 50
DEFAULT_GRAPH_RAG_EVIDENCE_PER_CLAIM = 5
MAX_GRAPH_RAG_EVIDENCE_PER_CLAIM = 10
MAX_GRAPH_RAG_FRONTIER = 100
MAX_GRAPH_RAG_CANDIDATE_EPISODES = 256
GRAPH_RAG_SCORE_CONFIDENCE_WEIGHT = 0.7
GRAPH_RAG_SCORE_EPISODE_SUPPORT_WEIGHT = 0.3
GRAPH_RAG_EPISODE_SUPPORT_SATURATION = 3


@dataclass(frozen=True, slots=True)
class GraphRagConfiguration:
    revision: str = GRAPH_RAG_QUERY_REVISION
    max_seeds: int = MAX_GRAPH_RAG_SEEDS
    max_predicates: int = MAX_GRAPH_RAG_PREDICATES
    max_hops: int = MAX_GRAPH_RAG_HOPS
    max_claims: int = MAX_GRAPH_RAG_CLAIMS
    max_evidence_per_claim: int = MAX_GRAPH_RAG_EVIDENCE_PER_CLAIM
    max_frontier: int = MAX_GRAPH_RAG_FRONTIER
    max_candidate_episodes: int = MAX_GRAPH_RAG_CANDIDATE_EPISODES

    def __post_init__(self) -> None:
        if self.revision != GRAPH_RAG_QUERY_REVISION:
            raise ValueError(GraphRagErrorMessages.CONFIGURATION_INVALID)
        values = (
            self.max_seeds,
            self.max_predicates,
            self.max_hops,
            self.max_claims,
            self.max_evidence_per_claim,
            self.max_frontier,
            self.max_candidate_episodes,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in values
        ):
            raise ValueError(GraphRagErrorMessages.CONFIGURATION_INVALID)
        if (
            self.max_seeds > MAX_GRAPH_RAG_SEEDS
            or self.max_predicates > MAX_GRAPH_RAG_PREDICATES
            or self.max_hops > MAX_GRAPH_RAG_HOPS
            or self.max_claims > MAX_GRAPH_RAG_CLAIMS
            or self.max_evidence_per_claim > MAX_GRAPH_RAG_EVIDENCE_PER_CLAIM
            or self.max_frontier > MAX_GRAPH_RAG_FRONTIER
            or self.max_frontier < self.max_seeds
            or self.max_candidate_episodes > MAX_GRAPH_RAG_CANDIDATE_EPISODES
        ):
            raise ValueError(GraphRagErrorMessages.CONFIGURATION_INVALID)


DEFAULT_GRAPH_RAG_CONFIGURATION = GraphRagConfiguration()
