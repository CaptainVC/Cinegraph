from dataclasses import replace
from uuid import UUID

from cinegraph.application.models.graph_rag import GraphRagQuery, GraphRagResult
from cinegraph.common.error_messages import GraphRagErrorMessages
from cinegraph.common.graph_normalization import normalize_graph_identity, normalize_graph_predicate
from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.config.graph_claims import (
    GRAPH_CLAIM_EXTRACTION_REVISION,
    MAX_GRAPH_NAME_LENGTH,
    MAX_GRAPH_PREDICATE_LENGTH,
)
from cinegraph.config.graph_rag import (
    DEFAULT_GRAPH_RAG_CONFIGURATION,
    GRAPH_RAG_EPISODE_SUPPORT_SATURATION,
    GRAPH_RAG_SCORE_CONFIDENCE_WEIGHT,
    GRAPH_RAG_SCORE_EPISODE_SUPPORT_WEIGHT,
    GraphRagConfiguration,
)
from cinegraph.config.transcript_chunking import TRANSCRIPT_INDEX_REVISION
from cinegraph.domain.enums.enum import (
    RightsStatus,
    SourceReviewStatus,
    SourceVersionStatus,
)
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.retrieval import RetrievalScope, RetrievalScopeCompiler
from cinegraph.ports.graph import GraphClaimReader
from cinegraph.ports.graph.graph_claim_read_models import GraphRagReadClaim


class GraphRagQueryService:
    def __init__(
        self,
        scope_compiler: RetrievalScopeCompiler,
        reader: GraphClaimReader,
        configuration: GraphRagConfiguration = DEFAULT_GRAPH_RAG_CONFIGURATION,
    ) -> None:
        self._scope_compiler = scope_compiler
        self._reader = reader
        self._configuration = configuration

    def execute(self, query: GraphRagQuery) -> GraphRagResult:
        self._validate_query(query)
        try:
            normalized_seeds = tuple(normalize_graph_identity(term) for term in query.seed_terms)
            normalized_predicates = tuple(
                normalize_graph_predicate(item) for item in query.predicates
            )
        except ValueError as error:
            raise ValueError(GraphRagErrorMessages.QUERY_INVALID) from error
        scope = self._scope_compiler.compile(
            series_id=query.series_id,
            candidate_episodes=query.candidate_episodes,
            watch_state=query.profile_watch_state,
            corpus_access_scope=query.corpus_access_scope,
        )
        if not scope.episode_scopes:
            return GraphRagResult(claims=())
        claims = self._reader.read(
            scope=scope,
            seed_terms=normalized_seeds,
            predicates=normalized_predicates,
            hops=query.hops,
            # Read a bounded candidate pool, then apply deterministic application ranking.
            claim_limit=self._configuration.max_claims,
            evidence_per_claim=query.evidence_per_claim,
            max_frontier=self._configuration.max_frontier,
        )
        validated = self._validate_results(claims, query, scope, normalized_seeds)
        ranked = sorted(
            (replace(item, score=self._score(item)) for item in validated),
            key=lambda item: (item.hop_distance, -item.score, item.claim_id.hex),
        )
        return GraphRagResult(claims=tuple(ranked[: query.claim_limit]))

    def _validate_query(self, query: GraphRagQuery) -> None:
        if not isinstance(query, GraphRagQuery):
            raise ValueError(GraphRagErrorMessages.QUERY_INVALID)
        cfg = self._configuration
        if len(query.seed_terms) > cfg.max_seeds or any(
            not isinstance(item, str) or not item.strip() or len(item) > MAX_GRAPH_NAME_LENGTH
            for item in query.seed_terms
        ):
            raise ValueError(GraphRagErrorMessages.QUERY_SEEDS_INVALID)
        try:
            normalized = tuple(normalize_graph_identity(item) for item in query.seed_terms)
        except ValueError as error:
            raise ValueError(GraphRagErrorMessages.QUERY_SEEDS_INVALID) from error
        if len(set(normalized)) != len(normalized):
            raise ValueError(GraphRagErrorMessages.QUERY_SEEDS_INVALID)
        if len(query.predicates) > cfg.max_predicates:
            raise ValueError(GraphRagErrorMessages.QUERY_PREDICATES_INVALID)
        try:
            normalized_predicates = tuple(
                normalize_graph_predicate(item) for item in query.predicates
            )
        except ValueError as error:
            raise ValueError(GraphRagErrorMessages.QUERY_PREDICATES_INVALID) from error
        if any(len(item) > MAX_GRAPH_PREDICATE_LENGTH for item in normalized_predicates):
            raise ValueError(GraphRagErrorMessages.QUERY_PREDICATES_INVALID)
        if len(set(normalized_predicates)) != len(normalized_predicates):
            raise ValueError(GraphRagErrorMessages.QUERY_PREDICATES_INVALID)
        if len(query.candidate_episodes) > cfg.max_candidate_episodes:
            raise ValueError(GraphRagErrorMessages.QUERY_EPISODES_INVALID)
        episode_ids = tuple(item.episode_id for item in query.candidate_episodes)
        if len(set(episode_ids)) != len(episode_ids) or any(
            item.series_id != query.series_id for item in query.candidate_episodes
        ):
            raise ValueError(GraphRagErrorMessages.QUERY_EPISODES_INVALID)
        if (
            query.hops > cfg.max_hops
            or query.claim_limit > cfg.max_claims
            or query.evidence_per_claim > cfg.max_evidence_per_claim
        ):
            raise ValueError(GraphRagErrorMessages.QUERY_LIMIT_INVALID)

    def _validate_results(
        self,
        claims: tuple[GraphRagReadClaim, ...],
        query: GraphRagQuery,
        scope: RetrievalScope,
        seeds: tuple[str, ...],
    ) -> tuple[GraphRagReadClaim, ...]:
        if (
            not isinstance(claims, tuple)
            or len(claims) > self._configuration.max_claims
            or any(not isinstance(item, GraphRagReadClaim) for item in claims)
        ):
            raise InvalidModelError(GraphRagErrorMessages.RESULT_LIMIT_INVALID)
        scope_by_episode = {item.episode.episode_id: item for item in scope.episode_scopes}
        claims = tuple(sorted(claims, key=lambda item: (item.hop_distance, item.claim_id.hex)))
        claim_ids: set[UUID] = set()
        entity_distance = {entity_id: 0 for entity_id in self._seed_entity_ids(claims, seeds)}
        for item in claims:
            if (
                item.claim_id in claim_ids
                or item.series_id != query.series_id
                or item.hop_distance < 1
                or item.hop_distance > query.hops
                or len(item.evidence) > query.evidence_per_claim
            ):
                raise InvalidModelError(GraphRagErrorMessages.RESULT_RELATIONSHIP_INVALID)
            claim_ids.add(item.claim_id)
            for entity in (item.subject, item.object):
                expected = IdentifierGenerator.graph_entity_id(
                    entity.series_id, entity.kind, entity.normalized_key
                )
                if entity.series_id != query.series_id or entity.entity_id != expected:
                    raise InvalidModelError(GraphRagErrorMessages.RESULT_RELATIONSHIP_INVALID)
            expected_claim = IdentifierGenerator.graph_claim_id(
                GRAPH_CLAIM_EXTRACTION_REVISION,
                item.series_id,
                item.subject.entity_id,
                item.predicate,
                item.object.entity_id,
                item.polarity,
            )
            if item.claim_id != expected_claim or not item.evidence:
                raise InvalidModelError(GraphRagErrorMessages.RESULT_RELATIONSHIP_INVALID)
            if item.evidence != tuple(
                sorted(
                    item.evidence,
                    key=lambda evidence: (
                        -evidence.confidence,
                        evidence.end_ms,
                        evidence.evidence_id.hex,
                    ),
                )
            ):
                raise InvalidModelError(GraphRagErrorMessages.RESULT_RELATIONSHIP_INVALID)
            evidence_ids: set[UUID] = set()
            for evidence in item.evidence:
                if evidence.evidence_id in evidence_ids or evidence.claim_id != item.claim_id:
                    raise InvalidModelError(GraphRagErrorMessages.RESULT_RELATIONSHIP_INVALID)
                evidence_ids.add(evidence.evidence_id)
                visibility = scope_by_episode.get(evidence.episode.episode_id)
                if (
                    visibility is None
                    or evidence.episode != visibility.episode
                    or (
                        visibility.safe_until_ms is not None
                        and evidence.end_ms > visibility.safe_until_ms
                    )
                ):
                    raise InvalidModelError(GraphRagErrorMessages.RESULT_SCOPE_INVALID)
                if (
                    evidence.rights_status is not RightsStatus.ALLOWED
                    or evidence.source_status is not SourceVersionStatus.ACTIVE
                    or evidence.review_status
                    not in {
                        SourceReviewStatus.AUTOMATED_REVIEWED,
                        SourceReviewStatus.HYBRID_REVIEWED,
                        SourceReviewStatus.REVIEWED,
                    }
                    or evidence.transcript_index_revision != TRANSCRIPT_INDEX_REVISION
                    or evidence.extraction_revision != GRAPH_CLAIM_EXTRACTION_REVISION
                ):
                    raise InvalidModelError(GraphRagErrorMessages.RESULT_SCOPE_INVALID)
                if evidence.evidence_id != IdentifierGenerator.graph_evidence_id(
                    evidence.claim_id, evidence.source_version_id, evidence.transcript_chunk_id
                ):
                    raise InvalidModelError(GraphRagErrorMessages.RESULT_RELATIONSHIP_INVALID)
            endpoint_distance = min(
                entity_distance.get(item.subject.entity_id, query.hops + 1),
                entity_distance.get(item.object.entity_id, query.hops + 1),
            )
            if endpoint_distance + 1 != item.hop_distance:
                raise InvalidModelError(GraphRagErrorMessages.RESULT_RELATIONSHIP_INVALID)
            entity_distance[item.subject.entity_id] = min(
                entity_distance.get(item.subject.entity_id, query.hops + 1), item.hop_distance
            )
            entity_distance[item.object.entity_id] = min(
                entity_distance.get(item.object.entity_id, query.hops + 1), item.hop_distance
            )
        return claims

    @staticmethod
    def _seed_entity_ids(
        claims: tuple[GraphRagReadClaim, ...], seeds: tuple[str, ...]
    ) -> set[UUID]:
        normalized = set(seeds)
        return {
            entity.entity_id
            for claim in claims
            for entity in (claim.subject, claim.object)
            if normalized.intersection(normalize_graph_identity(alias) for alias in entity.aliases)
        }

    @staticmethod
    def _score(claim: GraphRagReadClaim) -> float:
        support = max(item.confidence for item in claim.evidence)
        episode_support = min(
            1.0,
            len({item.episode.episode_id for item in claim.evidence})
            / GRAPH_RAG_EPISODE_SUPPORT_SATURATION,
        )
        return (
            GRAPH_RAG_SCORE_CONFIDENCE_WEIGHT * support
            + GRAPH_RAG_SCORE_EPISODE_SUPPORT_WEIGHT * episode_support
        )
