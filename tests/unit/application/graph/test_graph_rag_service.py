from dataclasses import replace
from uuid import uuid4

import pytest
from tests.factories import make_authenticated_corpus_access_scope

from cinegraph.application.models.graph_rag import GraphRagQuery
from cinegraph.application.service.graph_rag_service import GraphRagQueryService
from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.config.graph_claims import GRAPH_CLAIM_EXTRACTION_REVISION
from cinegraph.config.graph_rag import GraphRagConfiguration
from cinegraph.config.transcript_chunking import TRANSCRIPT_INDEX_REVISION
from cinegraph.domain.enums.enum import (
    GraphClaimPolarity,
    GraphEntityKind,
    RightsStatus,
    SourceReviewStatus,
    SourceVersionStatus,
)
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodePosition, EpisodeRef
from cinegraph.domain.retrieval.retrieval_scope import EpisodeVisibilityScope, RetrievalScope
from cinegraph.ports.graph.graph_claim_read_models import (
    GraphRagReadClaim,
    GraphRagReadEntity,
    GraphRagReadEvidence,
)


class _Compiler:
    def __init__(self, scope: RetrievalScope) -> None:
        self.scope = scope
        self.calls = 0

    def compile(self, **_: object) -> RetrievalScope:
        self.calls += 1
        return self.scope


class _Reader:
    def __init__(self, claims: tuple[GraphRagReadClaim, ...] = ()) -> None:
        self.claims = claims
        self.calls = 0
        self.arguments: list[dict[str, object]] = []

    def read(self, **arguments: object) -> tuple[GraphRagReadClaim, ...]:
        self.calls += 1
        self.arguments.append(arguments)
        return self.claims


class _MalformedReader:
    def __init__(self, result: object) -> None:
        self.result = result

    def read(self, **_: object) -> object:
        return self.result


def _make_claim(
    series_id,
    episodes: tuple[EpisodeRef, ...],
    *,
    object_name: str = "Sam",
    confidences: tuple[float, ...] = (0.9,),
    hop_distance: int = 1,
) -> GraphRagReadClaim:
    subject_id = IdentifierGenerator.graph_entity_id(series_id, GraphEntityKind.CHARACTER, "alex")
    object_key = object_name.casefold()
    object_id = IdentifierGenerator.graph_entity_id(series_id, GraphEntityKind.PERSON, object_key)
    claim_id = IdentifierGenerator.graph_claim_id(
        GRAPH_CLAIM_EXTRACTION_REVISION,
        series_id,
        subject_id,
        "knows",
        object_id,
        GraphClaimPolarity.ASSERTED,
    )
    evidence = []
    for episode, confidence in zip(episodes, confidences, strict=True):
        source_id, chunk_id = uuid4(), uuid4()
        evidence.append(
            GraphRagReadEvidence(
                IdentifierGenerator.graph_evidence_id(claim_id, source_id, chunk_id),
                claim_id,
                source_id,
                chunk_id,
                episode,
                0,
                1000,
                confidence,
                TRANSCRIPT_INDEX_REVISION,
                GRAPH_CLAIM_EXTRACTION_REVISION,
                RightsStatus.ALLOWED,
                SourceVersionStatus.ACTIVE,
                SourceReviewStatus.AUTOMATED_REVIEWED,
            )
        )
    ordered_evidence = tuple(
        sorted(evidence, key=lambda item: (-item.confidence, item.end_ms, item.evidence_id.hex))
    )
    return GraphRagReadClaim(
        claim_id,
        series_id,
        GraphRagReadEntity(
            subject_id,
            series_id,
            GraphEntityKind.CHARACTER,
            "alex",
            "Alex",
            ("Alex",),
        ),
        "knows",
        GraphRagReadEntity(
            object_id,
            series_id,
            GraphEntityKind.PERSON,
            object_key,
            object_name,
            (object_name,),
        ),
        GraphClaimPolarity.ASSERTED,
        hop_distance,
        max(confidences),
        ordered_evidence,
    )


def _fixture() -> tuple[GraphRagQuery, GraphRagReadClaim]:
    series_id, season_id, episode_id, source_id, chunk_id = (uuid4() for _ in range(5))
    episode = EpisodeRef(series_id, season_id, episode_id, EpisodePosition(1, 1))
    subject_id = IdentifierGenerator.graph_entity_id(series_id, GraphEntityKind.CHARACTER, "alex")
    object_id = IdentifierGenerator.graph_entity_id(series_id, GraphEntityKind.PERSON, "sam")
    claim_id = IdentifierGenerator.graph_claim_id(
        GRAPH_CLAIM_EXTRACTION_REVISION,
        series_id,
        subject_id,
        "knows",
        object_id,
        GraphClaimPolarity.ASSERTED,
    )
    evidence_id = IdentifierGenerator.graph_evidence_id(claim_id, source_id, chunk_id)
    evidence = GraphRagReadEvidence(
        evidence_id,
        claim_id,
        source_id,
        chunk_id,
        episode,
        0,
        1000,
        0.9,
        TRANSCRIPT_INDEX_REVISION,
        GRAPH_CLAIM_EXTRACTION_REVISION,
        RightsStatus.ALLOWED,
        SourceVersionStatus.ACTIVE,
        SourceReviewStatus.AUTOMATED_REVIEWED,
    )
    subject = GraphRagReadEntity(
        subject_id, series_id, GraphEntityKind.CHARACTER, "alex", "Alex", ("Alex",)
    )
    object_ = GraphRagReadEntity(
        object_id, series_id, GraphEntityKind.PERSON, "sam", "Sam", ("Sam",)
    )
    claim = GraphRagReadClaim(
        claim_id,
        series_id,
        subject,
        "knows",
        object_,
        GraphClaimPolarity.ASSERTED,
        1,
        0.9,
        (evidence,),
    )
    query = GraphRagQuery(
        series_id, ("Ａlex",), (episode,), make_authenticated_corpus_access_scope(), hops=2
    )
    return query, claim


def test_service_normalizes_seeds_and_ranks_after_trusted_scope_compilation() -> None:
    query, claim = _fixture()
    compiler = _Compiler(
        RetrievalScope(
            query.series_id, (EpisodeVisibilityScope(query.candidate_episodes[0], None),)
        )
    )
    reader = _Reader((claim,))
    result = GraphRagQueryService(compiler, reader).execute(query)
    assert result.claims[0].claim_id == claim.claim_id
    assert compiler.calls == 1
    assert reader.calls == 1


def test_empty_scope_returns_without_calling_reader() -> None:
    query, claim = _fixture()
    compiler = _Compiler(RetrievalScope(query.series_id, ()))
    reader = _Reader((claim,))
    assert GraphRagQueryService(compiler, reader).execute(query).claims == ()
    assert reader.calls == 0


def test_service_rejects_evidence_outside_compiled_scope() -> None:
    query, claim = _fixture()
    other_episode = EpisodeRef(query.series_id, uuid4(), uuid4(), EpisodePosition(3, 1))
    leaked = GraphRagReadEvidence(
        claim.evidence[0].evidence_id,
        claim.claim_id,
        claim.evidence[0].source_version_id,
        claim.evidence[0].transcript_chunk_id,
        other_episode,
        0,
        1000,
        0.9,
        TRANSCRIPT_INDEX_REVISION,
        GRAPH_CLAIM_EXTRACTION_REVISION,
        RightsStatus.ALLOWED,
        SourceVersionStatus.ACTIVE,
        SourceReviewStatus.AUTOMATED_REVIEWED,
    )
    leaked_claim = GraphRagReadClaim(
        claim.claim_id,
        claim.series_id,
        claim.subject,
        claim.predicate,
        claim.object,
        claim.polarity,
        1,
        claim.score,
        (leaked,),
    )
    compiler = _Compiler(
        RetrievalScope(
            query.series_id, (EpisodeVisibilityScope(query.candidate_episodes[0], None),)
        )
    )
    with pytest.raises(ValueError):
        GraphRagQueryService(compiler, _Reader((leaked_claim,))).execute(query)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda query: replace(query, seed_terms=("Alex", "ＡLEX")),
        lambda query: replace(query, seed_terms=tuple(str(index) for index in range(9))),
        lambda query: replace(query, predicates=("not/valid",)),
        lambda query: replace(query, candidate_episodes=query.candidate_episodes * 2),
        lambda query: replace(query, hops=3),
        lambda query: replace(query, claim_limit=51),
        lambda query: replace(query, evidence_per_claim=11),
    ],
)
def test_invalid_query_is_rejected_before_scope_compilation_or_reader(
    mutator,
) -> None:
    query, _ = _fixture()
    compiler = _Compiler(RetrievalScope(query.series_id, ()))
    reader = _Reader()

    with pytest.raises(ValueError):
        GraphRagQueryService(compiler, reader).execute(mutator(query))

    assert compiler.calls == 0
    assert reader.calls == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"series_id": "invalid"},
        {"corpus_access_scope": object()},
        {"profile_watch_state": object()},
        {"seed_terms": ()},
        {"seed_terms": ["Alex"]},
        {"predicates": ["knows"]},
        {"candidate_episodes": []},
        {"hops": True},
        {"claim_limit": 0},
    ],
)
def test_query_model_rejects_invalid_boundary_types_and_empty_values(
    changes: dict[str, object],
) -> None:
    query, _ = _fixture()
    with pytest.raises(InvalidModelError):
        replace(query, **changes)


def test_service_uses_configured_candidate_pool_before_output_ranking() -> None:
    query, claim = _fixture()
    compiler = _Compiler(
        RetrievalScope(
            query.series_id, (EpisodeVisibilityScope(query.candidate_episodes[0], None),)
        )
    )
    reader = _Reader((claim,))
    configuration = GraphRagConfiguration(max_claims=7)

    result = GraphRagQueryService(compiler, reader, configuration).execute(
        replace(query, claim_limit=1)
    )

    assert len(result.claims) == 1
    assert reader.arguments[0]["claim_limit"] == 7


def test_service_rejects_non_tuple_mislabeled_hop_evidence_overflow_and_order() -> None:
    query, claim = _fixture()
    scope = RetrievalScope(
        query.series_id, (EpisodeVisibilityScope(query.candidate_episodes[0], None),)
    )

    def service(reader):
        return GraphRagQueryService(_Compiler(scope), reader)

    with pytest.raises(InvalidModelError):
        service(_MalformedReader([claim])).execute(query)  # type: ignore[arg-type]
    with pytest.raises(InvalidModelError):
        service(_Reader((replace(claim, hop_distance=2),))).execute(query)

    source_id, chunk_id = uuid4(), uuid4()
    second_evidence = GraphRagReadEvidence(
        IdentifierGenerator.graph_evidence_id(claim.claim_id, source_id, chunk_id),
        claim.claim_id,
        source_id,
        chunk_id,
        query.candidate_episodes[0],
        0,
        900,
        0.8,
        TRANSCRIPT_INDEX_REVISION,
        GRAPH_CLAIM_EXTRACTION_REVISION,
        RightsStatus.ALLOWED,
        SourceVersionStatus.ACTIVE,
        SourceReviewStatus.AUTOMATED_REVIEWED,
    )
    ordered = replace(claim, evidence=(claim.evidence[0], second_evidence))
    with pytest.raises(InvalidModelError):
        service(_Reader((ordered,))).execute(replace(query, evidence_per_claim=1))
    with pytest.raises(InvalidModelError):
        service(_Reader((replace(claim, evidence=(second_evidence, claim.evidence[0])),))).execute(
            query
        )


def test_ranking_rewards_independent_episode_support_after_hop_distance() -> None:
    query, _ = _fixture()
    episodes = (
        query.candidate_episodes[0],
        EpisodeRef(query.series_id, uuid4(), uuid4(), EpisodePosition(1, 2)),
        EpisodeRef(query.series_id, uuid4(), uuid4(), EpisodePosition(1, 3)),
    )
    stronger_single = _make_claim(
        query.series_id,
        episodes[:1],
        object_name="Sam",
        confidences=(0.9,),
    )
    supported = _make_claim(
        query.series_id,
        episodes,
        object_name="Blair",
        confidences=(0.85, 0.85, 0.85),
    )
    scope = RetrievalScope(
        query.series_id, tuple(EpisodeVisibilityScope(item, None) for item in episodes)
    )

    result = GraphRagQueryService(_Compiler(scope), _Reader((stronger_single, supported))).execute(
        replace(query, candidate_episodes=episodes)
    )

    assert result.claims[0].object.display_name == "Blair"
    assert result.claims[0].score > result.claims[1].score
