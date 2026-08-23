from collections.abc import Iterator
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.orm import Session

from cinegraph.adapters.persistence.base import PersistenceBase
from cinegraph.adapters.persistence.sqlalchemy_graph_claim_store import (
    GraphClaimEvidenceRow,
    GraphClaimRow,
    GraphEntityAliasRow,
    GraphEntityRow,
    SqlAlchemyGraphClaimStore,
)
from cinegraph.common.error_messages import GraphErrorMessages
from cinegraph.common.graph_normalization import normalize_graph_identity
from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.config.graph_claims import GRAPH_CLAIM_EXTRACTION_REVISION
from cinegraph.config.transcript_chunking import TRANSCRIPT_INDEX_REVISION
from cinegraph.domain.enums.enum import (
    GraphClaimPolarity,
    GraphEntityKind,
    RightsStatus,
    SourceReviewStatus,
    SourceVersionStatus,
)
from cinegraph.domain.models.graph.graph_models import (
    GraphClaim,
    GraphClaimEvidence,
    GraphEntity,
)
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodePosition, EpisodeRef


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    PersistenceBase.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def _batch(
    *,
    source_id: UUID | None = None,
    series_id: UUID | None = None,
    chunk_id: UUID | None = None,
    confidence: float = 0.8,
    subject_aliases: tuple[str, ...] = ("Alex",),
) -> tuple[
    UUID,
    tuple[GraphEntity, ...],
    tuple[GraphClaim, ...],
    tuple[GraphClaimEvidence, ...],
]:
    source_id = source_id or uuid4()
    series_id = series_id or uuid4()
    chunk_id = chunk_id or uuid4()
    subject_key = normalize_graph_identity("Alex")
    object_key = normalize_graph_identity("Sam")
    subject_id = IdentifierGenerator.graph_entity_id(
        series_id, GraphEntityKind.CHARACTER, subject_key
    )
    object_id = IdentifierGenerator.graph_entity_id(series_id, GraphEntityKind.PERSON, object_key)
    entities = (
        GraphEntity(
            subject_id,
            series_id,
            GraphEntityKind.CHARACTER,
            subject_key,
            "Alex",
            subject_aliases,
        ),
        GraphEntity(
            object_id,
            series_id,
            GraphEntityKind.PERSON,
            object_key,
            "Sam",
            ("Sam",),
        ),
    )
    claim_id = IdentifierGenerator.graph_claim_id(
        GRAPH_CLAIM_EXTRACTION_REVISION,
        series_id,
        subject_id,
        "knows",
        object_id,
        GraphClaimPolarity.ASSERTED,
    )
    claims = (
        GraphClaim(
            claim_id,
            series_id,
            subject_id,
            "knows",
            object_id,
            GraphClaimPolarity.ASSERTED,
        ),
    )
    evidence = (
        GraphClaimEvidence(
            IdentifierGenerator.graph_evidence_id(claim_id, source_id, chunk_id),
            claim_id,
            source_id,
            chunk_id,
            EpisodeRef(series_id, uuid4(), uuid4(), EpisodePosition(1, 1)),
            0,
            1000,
            confidence,
            TRANSCRIPT_INDEX_REVISION,
            GRAPH_CLAIM_EXTRACTION_REVISION,
            RightsStatus.ALLOWED,
            SourceVersionStatus.ACTIVE,
            SourceReviewStatus.AUTOMATED_REVIEWED,
        ),
    )
    return source_id, entities, claims, evidence


def _counts(engine: Engine) -> tuple[int, int, int, int]:
    with Session(engine) as session:
        return (
            session.scalar(select(func.count()).select_from(GraphEntityRow)) or 0,
            session.scalar(select(func.count()).select_from(GraphEntityAliasRow)) or 0,
            session.scalar(select(func.count()).select_from(GraphClaimRow)) or 0,
            session.scalar(select(func.count()).select_from(GraphClaimEvidenceRow)) or 0,
        )


def test_store_commits_idempotently_and_alias_ids_are_order_independent(engine: Engine) -> None:
    source_id, entities, claims, evidence = _batch(subject_aliases=("Alex", "Al"))
    store = SqlAlchemyGraphClaimStore(engine)

    store.replace_source_version(source_id, None, entities, claims, evidence)
    store.replace_source_version(source_id, None, entities, claims, evidence)

    assert _counts(engine) == (2, 3, 1, 1)
    with Session(engine) as session:
        alias_ids = set(session.scalars(select(GraphEntityAliasRow.alias_id)))
    assert alias_ids == {
        IdentifierGenerator.graph_entity_alias_id(entities[0].entity_id, "Alex"),
        IdentifierGenerator.graph_entity_alias_id(entities[0].entity_id, "Al"),
        IdentifierGenerator.graph_entity_alias_id(entities[1].entity_id, "Sam"),
    }


def test_replacement_retires_only_parent_evidence_and_keeps_shared_claim(engine: Engine) -> None:
    store = SqlAlchemyGraphClaimStore(engine)
    first_source, entities, claims, first_evidence = _batch()
    second_source = uuid4()
    _, _, _, second_evidence = _batch(
        series_id=entities[0].series_id,
        source_id=second_source,
    )

    store.replace_source_version(first_source, None, entities, claims, first_evidence)
    store.replace_source_version(second_source, first_source, entities, claims, second_evidence)

    assert _counts(engine) == (2, 2, 1, 1)
    with Session(engine) as session:
        assert session.scalar(select(GraphClaimEvidenceRow.source_version_id)) == second_source


def test_empty_replacement_removes_orphan_claim_but_retains_entity_dictionary(
    engine: Engine,
) -> None:
    store = SqlAlchemyGraphClaimStore(engine)
    source_id, entities, claims, evidence = _batch()
    store.replace_source_version(source_id, None, entities, claims, evidence)

    store.replace_source_version(uuid4(), source_id, (), (), ())

    assert _counts(engine) == (2, 2, 0, 0)


def test_evidence_conflict_rolls_back_before_parent_is_retired(engine: Engine) -> None:
    store = SqlAlchemyGraphClaimStore(engine)
    source_id, entities, claims, evidence = _batch()
    store.replace_source_version(source_id, None, entities, claims, evidence)

    with pytest.raises(ValueError, match=GraphErrorMessages.EVIDENCE_METADATA_CONFLICT):
        store.replace_source_version(
            source_id,
            uuid4(),
            entities,
            claims,
            (replace(evidence[0], confidence=0.9),),
        )

    assert _counts(engine) == (2, 2, 1, 1)
    with Session(engine) as session:
        assert session.scalar(select(GraphClaimEvidenceRow.confidence)) == 0.8


@pytest.mark.parametrize(
    ("entities_mutator", "claims_mutator", "evidence_mutator", "message"),
    [
        (
            lambda entities: (replace(entities[0], entity_id=uuid4()), entities[1]),
            lambda claims: claims,
            lambda evidence: evidence,
            GraphErrorMessages.ENTITY_FIELDS_INVALID,
        ),
        (
            lambda entities: entities,
            lambda claims: (replace(claims[0], claim_id=uuid4()),),
            lambda evidence: evidence,
            GraphErrorMessages.CLAIM_FIELDS_INVALID,
        ),
        (
            lambda entities: entities,
            lambda claims: claims,
            lambda evidence: (replace(evidence[0], evidence_id=uuid4()),),
            GraphErrorMessages.EVIDENCE_FIELDS_INVALID,
        ),
    ],
)
def test_invalid_stable_ids_are_rejected_before_database_side_effects(
    engine: Engine,
    entities_mutator,
    claims_mutator,
    evidence_mutator,
    message: str,
) -> None:
    source_id, entities, claims, evidence = _batch()

    with pytest.raises(ValueError, match=message):
        SqlAlchemyGraphClaimStore(engine).replace_source_version(
            source_id,
            None,
            entities_mutator(entities),
            claims_mutator(claims),
            evidence_mutator(evidence),
        )

    assert _counts(engine) == (0, 0, 0, 0)


def test_batch_requires_exact_entity_and_evidence_coverage(engine: Engine) -> None:
    source_id, entities, claims, evidence = _batch()
    store = SqlAlchemyGraphClaimStore(engine)

    with pytest.raises(ValueError, match=GraphErrorMessages.ENTITY_FIELDS_INVALID):
        store.replace_source_version(source_id, None, entities + entities[:1], claims, evidence)
    with pytest.raises(ValueError, match=GraphErrorMessages.STORE_BATCH_INVALID):
        store.replace_source_version(source_id, None, entities, claims, ())
    with pytest.raises(ValueError, match=GraphErrorMessages.REPLACEMENT_INVALID):
        store.replace_source_version(source_id, source_id, entities, claims, evidence)

    assert _counts(engine) == (0, 0, 0, 0)
