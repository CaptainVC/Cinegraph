import pytest

from cinegraph.common.error_messages import GraphRagErrorMessages
from cinegraph.config.graph_rag import (
    DEFAULT_GRAPH_RAG_CONFIGURATION,
    GRAPH_RAG_EPISODE_SUPPORT_SATURATION,
    GRAPH_RAG_QUERY_REVISION,
    GRAPH_RAG_SCORE_CONFIDENCE_WEIGHT,
    GRAPH_RAG_SCORE_EPISODE_SUPPORT_WEIGHT,
    MAX_GRAPH_RAG_CANDIDATE_EPISODES,
    MAX_GRAPH_RAG_CLAIMS,
    MAX_GRAPH_RAG_EVIDENCE_PER_CLAIM,
    MAX_GRAPH_RAG_FRONTIER,
    MAX_GRAPH_RAG_HOPS,
    MAX_GRAPH_RAG_PREDICATES,
    MAX_GRAPH_RAG_SEEDS,
    GraphRagConfiguration,
)


def test_graph_rag_defaults_are_revisioned_bounded_and_weighted() -> None:
    configuration = DEFAULT_GRAPH_RAG_CONFIGURATION

    assert configuration.revision == GRAPH_RAG_QUERY_REVISION
    assert configuration.max_seeds == MAX_GRAPH_RAG_SEEDS
    assert configuration.max_predicates == MAX_GRAPH_RAG_PREDICATES
    assert configuration.max_hops == MAX_GRAPH_RAG_HOPS
    assert configuration.max_claims == MAX_GRAPH_RAG_CLAIMS
    assert configuration.max_evidence_per_claim == MAX_GRAPH_RAG_EVIDENCE_PER_CLAIM
    assert configuration.max_frontier == MAX_GRAPH_RAG_FRONTIER
    assert configuration.max_candidate_episodes == MAX_GRAPH_RAG_CANDIDATE_EPISODES
    assert GRAPH_RAG_SCORE_CONFIDENCE_WEIGHT + GRAPH_RAG_SCORE_EPISODE_SUPPORT_WEIGHT == 1
    assert GRAPH_RAG_EPISODE_SUPPORT_SATURATION > 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"revision": "old"},
        {"max_seeds": True},
        {"max_predicates": 0},
        {"max_hops": MAX_GRAPH_RAG_HOPS + 1},
        {"max_claims": MAX_GRAPH_RAG_CLAIMS + 1},
        {"max_evidence_per_claim": MAX_GRAPH_RAG_EVIDENCE_PER_CLAIM + 1},
        {"max_frontier": MAX_GRAPH_RAG_FRONTIER + 1},
        {"max_candidate_episodes": MAX_GRAPH_RAG_CANDIDATE_EPISODES + 1},
        {"max_seeds": 2, "max_frontier": 1},
    ],
)
def test_graph_rag_configuration_rejects_invalid_types_caps_and_relations(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match=GraphRagErrorMessages.CONFIGURATION_INVALID):
        GraphRagConfiguration(**kwargs)  # type: ignore[arg-type]
