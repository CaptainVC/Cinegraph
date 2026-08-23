from collections import defaultdict
from collections.abc import Mapping
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, aliased, sessionmaker

from cinegraph.adapters.persistence.sqlalchemy_graph_claim_store import (
    GraphClaimEvidenceRow,
    GraphClaimRow,
    GraphEntityAliasRow,
    GraphEntityRow,
)
from cinegraph.common.error_messages import GraphRagErrorMessages
from cinegraph.common.graph_normalization import normalize_graph_identity, normalize_graph_predicate
from cinegraph.config.graph_claims import (
    GRAPH_CLAIM_EXTRACTION_REVISION,
    MAX_GRAPH_NAME_LENGTH,
    MAX_GRAPH_PREDICATE_LENGTH,
)
from cinegraph.config.graph_rag import (
    MAX_GRAPH_RAG_CANDIDATE_EPISODES,
    MAX_GRAPH_RAG_CLAIMS,
    MAX_GRAPH_RAG_EVIDENCE_PER_CLAIM,
    MAX_GRAPH_RAG_FRONTIER,
    MAX_GRAPH_RAG_HOPS,
    MAX_GRAPH_RAG_PREDICATES,
    MAX_GRAPH_RAG_SEEDS,
)
from cinegraph.config.transcript_chunking import TRANSCRIPT_INDEX_REVISION
from cinegraph.domain.enums.enum import (
    GraphClaimPolarity,
    GraphEntityKind,
    RightsStatus,
    SourceReviewStatus,
    SourceVersionStatus,
)
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodePosition, EpisodeRef
from cinegraph.domain.retrieval.retrieval_scope import EpisodeVisibilityScope, RetrievalScope
from cinegraph.ports.graph.graph_claim_read_models import (
    GraphRagReadClaim,
    GraphRagReadEntity,
    GraphRagReadEvidence,
)


class SqlAlchemyGraphClaimReader:
    """Bounded, authorization-first relational GraphRAG traversal adapter."""

    def __init__(self, engine: Engine) -> None:
        self._session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def read(
        self,
        *,
        scope: RetrievalScope,
        seed_terms: tuple[str, ...],
        predicates: tuple[str, ...],
        hops: int,
        claim_limit: int,
        evidence_per_claim: int,
        max_frontier: int,
    ) -> tuple[GraphRagReadClaim, ...]:
        self._validate_request(
            scope, seed_terms, predicates, hops, claim_limit, evidence_per_claim, max_frontier
        )
        if not scope.episode_scopes:
            return ()
        with self._session_factory() as session:
            seeds = self._resolve_seed_entities(session, scope.series_id, seed_terms, max_frontier)
            if not seeds:
                return ()
            selected: dict[UUID, tuple[GraphClaimRow, GraphEntityRow, GraphEntityRow, int]] = {}
            frontier = seeds
            seen_entities = set(seeds)
            for distance in range(1, hops + 1):
                if not frontier:
                    break
                remaining = claim_limit - len(selected)
                if remaining <= 0:
                    break
                rows = self._load_frontier_claims(
                    session,
                    scope,
                    frontier,
                    frozenset(selected),
                    predicates,
                    remaining,
                )
                next_frontier: set[UUID] = set()
                for claim, subject, object_ in rows:
                    if claim.claim_id not in selected:
                        selected[claim.claim_id] = (claim, subject, object_, distance)
                    if distance < hops:
                        for entity_id in (subject.entity_id, object_.entity_id):
                            if entity_id not in seen_entities and len(seen_entities) < max_frontier:
                                seen_entities.add(entity_id)
                                next_frontier.add(entity_id)
                frontier = set(sorted(next_frontier, key=lambda item: item.hex))
            if not selected:
                return ()
            evidence = self._load_evidence(session, scope, tuple(selected), evidence_per_claim)
            aliases = self._load_aliases(
                session,
                {
                    entity.entity_id
                    for claim, subject, object_, _ in selected.values()
                    for entity in (subject, object_)
                },
            )
            claims: list[GraphRagReadClaim] = []
            for claim_id, (claim, subject, object_, distance) in selected.items():
                visible_evidence = evidence.get(claim_id, ())
                if not visible_evidence:
                    continue
                claims.append(
                    GraphRagReadClaim(
                        claim_id=claim.claim_id,
                        series_id=claim.series_id,
                        subject=_entity_dto(subject, aliases),
                        predicate=claim.predicate,
                        object=_entity_dto(object_, aliases),
                        polarity=GraphClaimPolarity(claim.polarity),
                        hop_distance=distance,
                        score=max(item.confidence for item in visible_evidence),
                        evidence=visible_evidence,
                    )
                )
            return tuple(
                sorted(
                    claims, key=lambda item: (item.hop_distance, -item.score, item.claim_id.hex)
                )[:claim_limit]
            )

    @staticmethod
    def _validate_request(
        scope: RetrievalScope,
        seeds: tuple[str, ...],
        predicates: tuple[str, ...],
        hops: int,
        claims: int,
        evidence: int,
        frontier: int,
    ) -> None:
        if (
            not isinstance(scope, RetrievalScope)
            or not isinstance(scope.series_id, UUID)
            or not isinstance(scope.episode_scopes, tuple)
            or len(scope.episode_scopes) > MAX_GRAPH_RAG_CANDIDATE_EPISODES
            or any(not isinstance(item, EpisodeVisibilityScope) for item in scope.episode_scopes)
            or any(
                not isinstance(item.episode, EpisodeRef)
                or (
                    item.safe_until_ms is not None
                    and (
                        isinstance(item.safe_until_ms, bool)
                        or not isinstance(item.safe_until_ms, int)
                        or item.safe_until_ms < 0
                    )
                )
                for item in scope.episode_scopes
            )
            or len({item.episode.episode_id for item in scope.episode_scopes})
            != len(scope.episode_scopes)
            or any(item.episode.series_id != scope.series_id for item in scope.episode_scopes)
            or not isinstance(seeds, tuple)
            or not seeds
            or len(seeds) > MAX_GRAPH_RAG_SEEDS
        ):
            raise ValueError(GraphRagErrorMessages.READER_BATCH_INVALID)
        if any(
            not isinstance(item, str) or not item.strip() or len(item) > MAX_GRAPH_NAME_LENGTH
            for item in seeds
        ):
            raise ValueError(GraphRagErrorMessages.READER_BATCH_INVALID)
        try:
            normalized_seeds = tuple(normalize_graph_identity(item) for item in seeds)
        except ValueError as error:
            raise ValueError(GraphRagErrorMessages.QUERY_SEEDS_INVALID) from error
        if len(set(normalized_seeds)) != len(normalized_seeds):
            raise ValueError(GraphRagErrorMessages.QUERY_SEEDS_INVALID)
        if not isinstance(predicates, tuple) or len(predicates) > MAX_GRAPH_RAG_PREDICATES:
            raise ValueError(GraphRagErrorMessages.QUERY_PREDICATES_INVALID)
        try:
            predicates_valid = all(
                isinstance(item, str)
                and len(item) <= MAX_GRAPH_PREDICATE_LENGTH
                and normalize_graph_predicate(item) == item
                for item in predicates
            )
        except ValueError as error:
            raise ValueError(GraphRagErrorMessages.QUERY_PREDICATES_INVALID) from error
        if not predicates_valid or len(set(predicates)) != len(predicates):
            raise ValueError(GraphRagErrorMessages.QUERY_PREDICATES_INVALID)
        if (
            any(
                isinstance(value, bool) or not isinstance(value, int) or value < 1
                for value in (hops, claims, evidence, frontier)
            )
            or hops > MAX_GRAPH_RAG_HOPS
            or claims > MAX_GRAPH_RAG_CLAIMS
            or evidence > MAX_GRAPH_RAG_EVIDENCE_PER_CLAIM
            or frontier > MAX_GRAPH_RAG_FRONTIER
        ):
            raise ValueError(GraphRagErrorMessages.QUERY_LIMIT_INVALID)

    @staticmethod
    def _resolve_seed_entities(
        session: Session, series_id: UUID, terms: tuple[str, ...], limit: int
    ) -> set[UUID]:
        normalized = tuple(normalize_graph_identity(item) for item in terms)
        rows = (
            session.execute(
                select(GraphEntityAliasRow.entity_id)
                .join(GraphEntityRow, GraphEntityRow.entity_id == GraphEntityAliasRow.entity_id)
                .where(
                    GraphEntityRow.series_id == series_id,
                    GraphEntityAliasRow.normalized_alias.in_(normalized),
                )
                .order_by(GraphEntityAliasRow.entity_id)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return set(rows)

    @staticmethod
    def _load_aliases(session: Session, entity_ids: set[UUID]) -> dict[UUID, tuple[str, ...]]:
        rows = session.execute(
            select(GraphEntityAliasRow.entity_id, GraphEntityAliasRow.alias)
            .where(GraphEntityAliasRow.entity_id.in_(entity_ids))
            .order_by(GraphEntityAliasRow.entity_id, GraphEntityAliasRow.normalized_alias)
        ).all()
        grouped: dict[UUID, list[str]] = defaultdict(list)
        for entity_id, alias in rows:
            grouped[entity_id].append(alias)
        return {entity_id: tuple(values) for entity_id, values in grouped.items()}

    @staticmethod
    def _visibility(table: type[GraphClaimEvidenceRow], scope: RetrievalScope) -> Any:
        episode_conditions = []
        for item in scope.episode_scopes:
            conditions = [
                table.episode_id == item.episode.episode_id,
                table.season_id == item.episode.season_id,
                table.series_id == scope.series_id,
                table.season_number == item.episode.position.season_number,
                table.episode_number == item.episode.position.episode_number,
            ]
            if item.safe_until_ms is not None:
                conditions.append(table.end_ms <= item.safe_until_ms)
            episode_conditions.append(and_(*conditions))
        return or_(*episode_conditions)

    @classmethod
    def _load_frontier_claims(
        cls,
        session: Session,
        scope: RetrievalScope,
        frontier: set[UUID],
        selected_claim_ids: frozenset[UUID],
        predicates: tuple[str, ...],
        limit: int,
    ) -> list[tuple[GraphClaimRow, GraphEntityRow, GraphEntityRow]]:
        subject = aliased(GraphEntityRow)
        object_ = aliased(GraphEntityRow)
        visible_evidence = exists(
            select(GraphClaimEvidenceRow.evidence_id).where(
                GraphClaimEvidenceRow.claim_id == GraphClaimRow.claim_id,
                GraphClaimEvidenceRow.transcript_index_revision == TRANSCRIPT_INDEX_REVISION,
                GraphClaimEvidenceRow.extraction_revision == GRAPH_CLAIM_EXTRACTION_REVISION,
                GraphClaimEvidenceRow.rights_status == RightsStatus.ALLOWED.value,
                GraphClaimEvidenceRow.source_status == SourceVersionStatus.ACTIVE.value,
                GraphClaimEvidenceRow.review_status.in_(
                    [
                        SourceReviewStatus.AUTOMATED_REVIEWED.value,
                        SourceReviewStatus.HYBRID_REVIEWED.value,
                        SourceReviewStatus.REVIEWED.value,
                    ]
                ),
                cls._visibility(GraphClaimEvidenceRow, scope),
            )
        )
        statement = (
            select(GraphClaimRow, subject, object_)
            .join(subject, subject.entity_id == GraphClaimRow.subject_entity_id)
            .join(object_, object_.entity_id == GraphClaimRow.object_entity_id)
            .where(
                GraphClaimRow.series_id == scope.series_id,
                GraphClaimRow.extraction_revision == GRAPH_CLAIM_EXTRACTION_REVISION,
                or_(
                    GraphClaimRow.subject_entity_id.in_(frontier),
                    GraphClaimRow.object_entity_id.in_(frontier),
                ),
                visible_evidence,
            )
            .order_by(GraphClaimRow.claim_id)
            .limit(limit)
        )
        if predicates:
            statement = statement.where(GraphClaimRow.predicate.in_(predicates))
        if selected_claim_ids:
            statement = statement.where(GraphClaimRow.claim_id.not_in(selected_claim_ids))
        return cast(
            list[tuple[GraphClaimRow, GraphEntityRow, GraphEntityRow]],
            session.execute(statement).all(),
        )

    @classmethod
    def _load_evidence(
        cls, session: Session, scope: RetrievalScope, claim_ids: tuple[UUID, ...], limit: int
    ) -> dict[UUID, tuple[GraphRagReadEvidence, ...]]:
        ranked = (
            select(
                GraphClaimEvidenceRow,
                func.row_number()
                .over(
                    partition_by=GraphClaimEvidenceRow.claim_id,
                    order_by=(
                        GraphClaimEvidenceRow.confidence.desc(),
                        GraphClaimEvidenceRow.end_ms,
                        GraphClaimEvidenceRow.evidence_id,
                    ),
                )
                .label("evidence_rank"),
            )
            .where(
                GraphClaimEvidenceRow.claim_id.in_(claim_ids),
                GraphClaimEvidenceRow.transcript_index_revision == TRANSCRIPT_INDEX_REVISION,
                GraphClaimEvidenceRow.extraction_revision == GRAPH_CLAIM_EXTRACTION_REVISION,
                GraphClaimEvidenceRow.rights_status == RightsStatus.ALLOWED.value,
                GraphClaimEvidenceRow.source_status == SourceVersionStatus.ACTIVE.value,
                GraphClaimEvidenceRow.review_status.in_(
                    [
                        SourceReviewStatus.AUTOMATED_REVIEWED.value,
                        SourceReviewStatus.HYBRID_REVIEWED.value,
                        SourceReviewStatus.REVIEWED.value,
                    ]
                ),
                cls._visibility(GraphClaimEvidenceRow, scope),
            )
            .subquery()
        )
        rows = (
            session.execute(
                select(ranked)
                .where(ranked.c.evidence_rank <= limit)
                .order_by(ranked.c.claim_id, ranked.c.evidence_rank)
            )
            .mappings()
            .all()
        )
        grouped: dict[UUID, list[GraphRagReadEvidence]] = defaultdict(list)
        for row in rows:
            grouped[row.claim_id].append(_evidence_dto(row))
        return {claim_id: tuple(items) for claim_id, items in grouped.items()}


def _entity_dto(
    row: GraphEntityRow, aliases_by_entity: dict[UUID, tuple[str, ...]]
) -> GraphRagReadEntity:
    return GraphRagReadEntity(
        entity_id=row.entity_id,
        series_id=row.series_id,
        kind=GraphEntityKind(row.kind),
        normalized_key=row.normalized_key,
        display_name=row.display_name,
        aliases=aliases_by_entity.get(row.entity_id, ()),
    )


def _evidence_dto(row: Mapping[Any, Any]) -> GraphRagReadEvidence:
    return GraphRagReadEvidence(
        evidence_id=row["evidence_id"],
        claim_id=row["claim_id"],
        source_version_id=row["source_version_id"],
        transcript_chunk_id=row["transcript_chunk_id"],
        episode=EpisodeRef(
            row["series_id"],
            row["season_id"],
            row["episode_id"],
            EpisodePosition(row["season_number"], row["episode_number"]),
        ),
        start_ms=row["start_ms"],
        end_ms=row["end_ms"],
        confidence=row["confidence"],
        transcript_index_revision=row["transcript_index_revision"],
        extraction_revision=row["extraction_revision"],
        rights_status=RightsStatus(row["rights_status"]),
        source_status=SourceVersionStatus(row["source_status"]),
        review_status=SourceReviewStatus(row["review_status"]),
    )
