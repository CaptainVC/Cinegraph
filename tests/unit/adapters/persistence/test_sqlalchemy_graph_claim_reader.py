from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine
from tests.factories import make_authenticated_corpus_access_scope

from cinegraph.adapters.persistence.base import PersistenceBase
from cinegraph.adapters.persistence.sqlalchemy_graph_claim_reader import SqlAlchemyGraphClaimReader
from cinegraph.adapters.persistence.sqlalchemy_graph_claim_store import SqlAlchemyGraphClaimStore
from cinegraph.application.models.graph_rag import GraphRagQuery
from cinegraph.application.service.graph_rag_service import GraphRagQueryService
from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.config import DEFAULT_GUEST_ACCESS_CONFIGURATION, DEFAULT_GUEST_CORPUS_ACCESS_SCOPE
from cinegraph.config.graph_claims import GRAPH_CLAIM_EXTRACTION_REVISION
from cinegraph.config.transcript_chunking import TRANSCRIPT_INDEX_REVISION
from cinegraph.domain.enums.enum import (
    GraphClaimPolarity,
    GraphEntityKind,
    RightsStatus,
    SourceReviewStatus,
    SourceVersionStatus,
    SpoilerMode,
)
from cinegraph.domain.models.graph.graph_models import GraphClaim, GraphClaimEvidence, GraphEntity
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodePosition, EpisodeRef
from cinegraph.domain.models.watch_state.profile_watch_state import ProfileWatchState
from cinegraph.domain.policy.spoiler_policy import SpoilerPolicy
from cinegraph.domain.retrieval.retrieval_scope import EpisodeVisibilityScope, RetrievalScope
from cinegraph.domain.retrieval.retrieval_scope_compiler import RetrievalScopeCompiler


def _fixture(series_id: UUID | None = None) -> tuple[Engine, UUID, tuple[EpisodeRef, ...]]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    PersistenceBase.metadata.create_all(engine)
    series_id, source_id = series_id or uuid4(), uuid4()
    entities = []
    for name in ("Alex", "Blair", "Casey", "Drew"):
        key = name.casefold()
        entity_id = IdentifierGenerator.graph_entity_id(series_id, GraphEntityKind.CHARACTER, key)
        entities.append(
            GraphEntity(entity_id, series_id, GraphEntityKind.CHARACTER, key, name, (name,))
        )
    episodes = tuple(
        EpisodeRef(series_id, uuid4(), uuid4(), EpisodePosition(season, 1)) for season in (1, 2, 3)
    )
    claims, evidence = [], []
    for index, episode in enumerate(episodes):
        claim_id = IdentifierGenerator.graph_claim_id(
            GRAPH_CLAIM_EXTRACTION_REVISION,
            series_id,
            entities[index].entity_id,
            "knows",
            entities[index + 1].entity_id,
            GraphClaimPolarity.ASSERTED,
        )
        chunk_id = uuid4()
        claims.append(
            GraphClaim(
                claim_id,
                series_id,
                entities[index].entity_id,
                "knows",
                entities[index + 1].entity_id,
                GraphClaimPolarity.ASSERTED,
            )
        )
        evidence.append(
            GraphClaimEvidence(
                IdentifierGenerator.graph_evidence_id(claim_id, source_id, chunk_id),
                claim_id,
                source_id,
                chunk_id,
                episode,
                0,
                1000,
                0.8,
                TRANSCRIPT_INDEX_REVISION,
                GRAPH_CLAIM_EXTRACTION_REVISION,
                RightsStatus.ALLOWED,
                SourceVersionStatus.ACTIVE,
                SourceReviewStatus.AUTOMATED_REVIEWED,
            )
        )
    SqlAlchemyGraphClaimStore(engine).replace_source_version(
        source_id, None, tuple(entities), tuple(claims), tuple(evidence)
    )
    return engine, series_id, episodes


@dataclass(frozen=True, slots=True)
class _Edge:
    subject: str
    object: str
    season_number: int
    polarity: GraphClaimPolarity = GraphClaimPolarity.ASSERTED
    predicate: str = "knows"
    confidence: float = 0.8
    end_ms: int = 1000


def _custom_fixture(
    edges: tuple[_Edge, ...],
) -> tuple[Engine, UUID, dict[int, EpisodeRef]]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    PersistenceBase.metadata.create_all(engine)
    series_id, source_id = uuid4(), uuid4()
    episodes = {
        season_number: EpisodeRef(
            series_id,
            uuid4(),
            uuid4(),
            EpisodePosition(season_number, 1),
        )
        for season_number in {edge.season_number for edge in edges}
    }
    names = {name for edge in edges for name in (edge.subject, edge.object)}
    entities_by_name = {
        name: GraphEntity(
            IdentifierGenerator.graph_entity_id(
                series_id,
                GraphEntityKind.CHARACTER,
                name.casefold(),
            ),
            series_id,
            GraphEntityKind.CHARACTER,
            name.casefold(),
            name,
            (name,),
        )
        for name in names
    }
    claims_by_id: dict[UUID, GraphClaim] = {}
    evidence: list[GraphClaimEvidence] = []
    for edge in edges:
        subject = entities_by_name[edge.subject]
        object_ = entities_by_name[edge.object]
        claim_id = IdentifierGenerator.graph_claim_id(
            GRAPH_CLAIM_EXTRACTION_REVISION,
            series_id,
            subject.entity_id,
            edge.predicate,
            object_.entity_id,
            edge.polarity,
        )
        claims_by_id.setdefault(
            claim_id,
            GraphClaim(
                claim_id,
                series_id,
                subject.entity_id,
                edge.predicate,
                object_.entity_id,
                edge.polarity,
            ),
        )
        chunk_id = uuid4()
        evidence.append(
            GraphClaimEvidence(
                IdentifierGenerator.graph_evidence_id(claim_id, source_id, chunk_id),
                claim_id,
                source_id,
                chunk_id,
                episodes[edge.season_number],
                0,
                edge.end_ms,
                edge.confidence,
                TRANSCRIPT_INDEX_REVISION,
                GRAPH_CLAIM_EXTRACTION_REVISION,
                RightsStatus.ALLOWED,
                SourceVersionStatus.ACTIVE,
                SourceReviewStatus.AUTOMATED_REVIEWED,
            )
        )
    SqlAlchemyGraphClaimStore(engine).replace_source_version(
        source_id,
        None,
        tuple(entities_by_name.values()),
        tuple(claims_by_id.values()),
        tuple(evidence),
    )
    return engine, series_id, episodes


def test_reader_guest_scope_never_bridges_into_invisible_season() -> None:
    engine, series_id, episodes = _fixture()
    scope = RetrievalScope(
        series_id, tuple(EpisodeVisibilityScope(item, None) for item in episodes[:2])
    )
    claims = SqlAlchemyGraphClaimReader(engine).read(
        scope=scope,
        seed_terms=("alex",),
        predicates=(),
        hops=2,
        claim_limit=50,
        evidence_per_claim=5,
        max_frontier=100,
    )
    assert [item.evidence[0].episode.position.season_number for item in claims] == [1, 2]
    assert all(item.evidence[0].episode.position.season_number != 3 for item in claims)
    engine.dispose()


def test_reader_partial_safe_until_excludes_late_evidence() -> None:
    engine, series_id, episodes = _fixture()
    scope = RetrievalScope(series_id, (EpisodeVisibilityScope(episodes[0], 500),))
    assert (
        SqlAlchemyGraphClaimReader(engine).read(
            scope=scope,
            seed_terms=("alex",),
            predicates=(),
            hops=2,
            claim_limit=50,
            evidence_per_claim=5,
            max_frontier=100,
        )
        == ()
    )
    engine.dispose()


def test_reader_resolves_casefold_nfkc_aliases_and_both_edge_directions() -> None:
    engine, series_id, episodes = _fixture()
    scope = RetrievalScope(series_id, (EpisodeVisibilityScope(episodes[0], None),))
    reader = SqlAlchemyGraphClaimReader(engine)
    assert (
        len(
            reader.read(
                scope=scope,
                seed_terms=("ＡLEX",),
                predicates=(),
                hops=1,
                claim_limit=50,
                evidence_per_claim=5,
                max_frontier=100,
            )
        )
        == 1
    )
    assert (
        len(
            reader.read(
                scope=scope,
                seed_terms=("blair",),
                predicates=(),
                hops=1,
                claim_limit=50,
                evidence_per_claim=5,
                max_frontier=100,
            )
        )
        == 1
    )
    assert (
        reader.read(
            scope=scope,
            seed_terms=("not-present",),
            predicates=(),
            hops=2,
            claim_limit=50,
            evidence_per_claim=5,
            max_frontier=100,
        )
        == ()
    )
    engine.dispose()


def test_reader_predicate_filter_and_retries_are_deterministic() -> None:
    engine, series_id, episodes = _fixture()
    scope = RetrievalScope(
        series_id, tuple(EpisodeVisibilityScope(item, None) for item in episodes[:2])
    )
    reader = SqlAlchemyGraphClaimReader(engine)
    first = reader.read(
        scope=scope,
        seed_terms=("alex",),
        predicates=("knows",),
        hops=2,
        claim_limit=50,
        evidence_per_claim=5,
        max_frontier=100,
    )
    assert first == reader.read(
        scope=scope,
        seed_terms=("alex",),
        predicates=("knows",),
        hops=2,
        claim_limit=50,
        evidence_per_claim=5,
        max_frontier=100,
    )
    assert (
        reader.read(
            scope=scope,
            seed_terms=("alex",),
            predicates=("likes",),
            hops=2,
            claim_limit=50,
            evidence_per_claim=5,
            max_frontier=100,
        )
        == ()
    )
    engine.dispose()


def test_authenticated_scope_can_read_a_later_season_through_application_service() -> None:
    engine, series_id, episodes = _fixture()
    profile = ProfileWatchState(uuid4(), "test", spoiler_mode=SpoilerMode.RELAXED)
    query = GraphRagQuery(
        series_id,
        ("casey",),
        episodes,
        make_authenticated_corpus_access_scope(),
        profile_watch_state=profile,
        hops=1,
    )
    result = GraphRagQueryService(
        RetrievalScopeCompiler(SpoilerPolicy()), SqlAlchemyGraphClaimReader(engine)
    ).execute(query)
    assert result.claims
    assert 3 in {item.evidence[0].episode.position.season_number for item in result.claims}
    engine.dispose()


def test_guest_scope_through_real_compiler_never_returns_season_three() -> None:
    engine, series_id, episodes = _fixture(DEFAULT_GUEST_ACCESS_CONFIGURATION.series_id)
    profile = ProfileWatchState(uuid4(), "test", spoiler_mode=SpoilerMode.RELAXED)
    query = GraphRagQuery(
        series_id,
        ("alex",),
        episodes,
        DEFAULT_GUEST_CORPUS_ACCESS_SCOPE,
        profile_watch_state=profile,
        hops=2,
    )
    result = GraphRagQueryService(
        RetrievalScopeCompiler(SpoilerPolicy()), SqlAlchemyGraphClaimReader(engine)
    ).execute(query)
    assert result.claims
    assert {item.evidence[0].episode.position.season_number for item in result.claims} == {1, 2}
    engine.dispose()


def test_reader_rejects_oversized_direct_requests() -> None:
    engine, series_id, episodes = _fixture()
    reader = SqlAlchemyGraphClaimReader(engine)
    scope = RetrievalScope(series_id, (EpisodeVisibilityScope(episodes[0], None),))
    with pytest.raises(ValueError):
        reader.read(
            scope=scope,
            seed_terms=tuple("x" for _ in range(9)),
            predicates=(),
            hops=1,
            claim_limit=50,
            evidence_per_claim=5,
            max_frontier=100,
        )
    with pytest.raises(ValueError):
        reader.read(
            scope=scope,
            seed_terms=("alex",),
            predicates=(),
            hops=3,
            claim_limit=50,
            evidence_per_claim=5,
            max_frontier=100,
        )
    engine.dispose()


def test_invisible_first_hop_cannot_bridge_to_visible_second_hop() -> None:
    engine, series_id, episodes = _custom_fixture(
        (
            _Edge("Alex", "Blair", 3),
            _Edge("Blair", "Casey", 1),
        )
    )
    reader = SqlAlchemyGraphClaimReader(engine)
    try:
        season_one = RetrievalScope(series_id, (EpisodeVisibilityScope(episodes[1], None),))
        assert (
            reader.read(
                scope=season_one,
                seed_terms=("alex",),
                predicates=(),
                hops=2,
                claim_limit=50,
                evidence_per_claim=5,
                max_frontier=100,
            )
            == ()
        )
        unrestricted = RetrievalScope(
            series_id,
            tuple(EpisodeVisibilityScope(item, None) for item in episodes.values()),
        )
        assert [
            item.hop_distance
            for item in reader.read(
                scope=unrestricted,
                seed_terms=("alex",),
                predicates=(),
                hops=2,
                claim_limit=50,
                evidence_per_claim=5,
                max_frontier=100,
            )
        ] == [1, 2]
    finally:
        engine.dispose()


def test_reader_preserves_conflicting_polarities_and_deduplicates_cycles() -> None:
    engine, series_id, episodes = _custom_fixture(
        (
            _Edge("Alex", "Blair", 1, GraphClaimPolarity.ASSERTED),
            _Edge("Alex", "Blair", 1, GraphClaimPolarity.NEGATED),
            _Edge("Blair", "Alex", 1),
            _Edge("Blair", "Casey", 1),
            _Edge("Casey", "Alex", 1),
        )
    )
    try:
        claims = SqlAlchemyGraphClaimReader(engine).read(
            scope=RetrievalScope(series_id, (EpisodeVisibilityScope(episodes[1], None),)),
            seed_terms=("alex",),
            predicates=(),
            hops=2,
            claim_limit=50,
            evidence_per_claim=5,
            max_frontier=100,
        )
        assert len({item.claim_id for item in claims}) == len(claims) == 5
        assert {item.polarity for item in claims if item.subject.display_name == "Alex"} >= {
            GraphClaimPolarity.ASSERTED,
            GraphClaimPolarity.NEGATED,
        }
        assert {item.hop_distance for item in claims} == {1, 2}
    finally:
        engine.dispose()


def test_selected_cycle_edges_do_not_consume_the_next_hop_limit() -> None:
    engine, series_id, episodes = _custom_fixture(
        (
            _Edge("Alex", "Blair", 1),
            _Edge("Alex", "Casey", 1),
            _Edge("Alex", "Drew", 1),
            _Edge("Blair", "Evan", 1),
            _Edge("Casey", "Fran", 1),
            _Edge("Drew", "Gail", 1),
        )
    )
    try:
        claims = SqlAlchemyGraphClaimReader(engine).read(
            scope=RetrievalScope(series_id, (EpisodeVisibilityScope(episodes[1], None),)),
            seed_terms=("alex",),
            predicates=(),
            hops=2,
            claim_limit=4,
            evidence_per_claim=5,
            max_frontier=100,
        )
        assert len(claims) == 4
        assert [item.hop_distance for item in claims].count(2) == 1
    finally:
        engine.dispose()


def test_evidence_window_cap_order_and_safe_until_boundary_are_deterministic() -> None:
    engine, series_id, episodes = _custom_fixture(
        (
            _Edge("Alex", "Blair", 1, confidence=0.7, end_ms=400),
            _Edge("Alex", "Blair", 1, confidence=0.9, end_ms=500),
            _Edge("Alex", "Blair", 1, confidence=0.9, end_ms=501),
        )
    )
    reader = SqlAlchemyGraphClaimReader(engine)
    try:
        bounded = reader.read(
            scope=RetrievalScope(series_id, (EpisodeVisibilityScope(episodes[1], 500),)),
            seed_terms=("alex",),
            predicates=(),
            hops=1,
            claim_limit=50,
            evidence_per_claim=2,
            max_frontier=100,
        )
        assert [(item.confidence, item.end_ms) for item in bounded[0].evidence] == [
            (0.9, 500),
            (0.7, 400),
        ]
        all_visible = reader.read(
            scope=RetrievalScope(series_id, (EpisodeVisibilityScope(episodes[1], None),)),
            seed_terms=("alex",),
            predicates=(),
            hops=1,
            claim_limit=50,
            evidence_per_claim=2,
            max_frontier=100,
        )
        assert [(item.confidence, item.end_ms) for item in all_visible[0].evidence] == [
            (0.9, 500),
            (0.9, 501),
        ]
        assert all_visible == reader.read(
            scope=RetrievalScope(series_id, (EpisodeVisibilityScope(episodes[1], None),)),
            seed_terms=("alex",),
            predicates=(),
            hops=1,
            claim_limit=50,
            evidence_per_claim=2,
            max_frontier=100,
        )
    finally:
        engine.dispose()


def test_reader_rejects_oversized_or_malformed_scope_before_sql() -> None:
    engine, series_id, episodes = _fixture()
    reader = SqlAlchemyGraphClaimReader(engine)
    try:
        oversized = RetrievalScope(
            series_id,
            tuple(
                EpisodeVisibilityScope(
                    EpisodeRef(series_id, uuid4(), uuid4(), EpisodePosition(1, index + 1)),
                    None,
                )
                for index in range(257)
            ),
        )
        with pytest.raises(ValueError):
            reader.read(
                scope=oversized,
                seed_terms=("alex",),
                predicates=(),
                hops=1,
                claim_limit=50,
                evidence_per_claim=5,
                max_frontier=100,
            )
        with pytest.raises(ValueError):
            reader.read(
                scope=RetrievalScope("invalid", ()),  # type: ignore[arg-type]
                seed_terms=("alex",),
                predicates=(),
                hops=1,
                claim_limit=50,
                evidence_per_claim=5,
                max_frontier=100,
            )
    finally:
        engine.dispose()
