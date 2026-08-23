from tests.unit.adapters.persistence.test_sqlalchemy_graph_claim_reader import _fixture

from cinegraph.adapters.persistence.sqlalchemy_graph_claim_reader import SqlAlchemyGraphClaimReader
from cinegraph.domain.retrieval.retrieval_scope import EpisodeVisibilityScope, RetrievalScope


def test_synthetic_graphrag_gate_reports_recall_and_zero_scope_leaks() -> None:
    engine, series_id, episodes = _fixture()
    try:
        scope = RetrievalScope(series_id, tuple(EpisodeVisibilityScope(item, None) for item in episodes[:2]))
        claims = SqlAlchemyGraphClaimReader(engine).read(scope=scope, seed_terms=("alex",), predicates=("knows",), hops=2, claim_limit=25, evidence_per_claim=5, max_frontier=100)
        expected_visible_seasons = {1, 2}
        returned_seasons = {item.evidence[0].episode.position.season_number for item in claims}
        hit = int(bool(claims))
        recall = len(returned_seasons & expected_visible_seasons) / len(expected_visible_seasons)
        forbidden_leaks = returned_seasons.intersection({3})
        assert hit == 1
        assert recall == 1.0
        assert forbidden_leaks == set()
    finally:
        engine.dispose()
