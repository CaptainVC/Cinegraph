import os
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cinegraph.adapters.identity import create_identity_engine
from cinegraph.adapters.persistence.migration_runner import upgrade_database
from cinegraph.adapters.persistence.sqlalchemy_graph_claim_store import (
    GraphClaimEvidenceRow,
    GraphClaimRow,
    GraphEntityRow,
    SqlAlchemyGraphClaimStore,
)
from cinegraph.common.error_messages import GraphErrorMessages
from cinegraph.common.graph_normalization import normalize_graph_identity
from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.config import CinegraphRuntimeSettings
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


def test_postgres_graph_claim_commit_retry_replacement_and_rollback() -> None:
    url = os.environ.get("CINEGRAPH_TEST_DATABASE_URL")
    if not url:
        pytest.skip("CINEGRAPH_TEST_DATABASE_URL is not configured")
    settings = CinegraphRuntimeSettings(_env_file=None, database_url=url, qdrant_mode="local")
    upgrade_database(settings)
    engine = create_identity_engine(settings)
    series_id = uuid4()
    first_source = uuid4()
    first_chunk = uuid4()
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
            ("Alex",),
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
    episode = EpisodeRef(series_id, uuid4(), uuid4(), EpisodePosition(1, 1))

    def evidence(source_id, chunk_id, confidence: float = 0.8):
        return (
            GraphClaimEvidence(
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
            ),
        )

    store = SqlAlchemyGraphClaimStore(engine)
    try:
        first_evidence = evidence(first_source, first_chunk)
        store.replace_source_version(first_source, None, entities, claims, first_evidence)
        store.replace_source_version(first_source, None, entities, claims, first_evidence)
        second_source, second_chunk = uuid4(), uuid4()
        store.replace_source_version(
            second_source,
            first_source,
            entities,
            claims,
            evidence(second_source, second_chunk),
        )
        with Session(engine) as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(GraphEntityRow)
                    .where(GraphEntityRow.series_id == series_id)
                )
                == 2
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(GraphClaimRow)
                    .where(GraphClaimRow.series_id == series_id)
                )
                == 1
            )
            rows = tuple(
                session.scalars(
                    select(GraphClaimEvidenceRow).where(
                        GraphClaimEvidenceRow.series_id == series_id
                    )
                )
            )
            assert len(rows) == 1
            assert rows[0].source_version_id == second_source

        with pytest.raises(ValueError, match=GraphErrorMessages.EVIDENCE_METADATA_CONFLICT):
            store.replace_source_version(
                second_source,
                uuid4(),
                entities,
                claims,
                (replace(evidence(second_source, second_chunk)[0], confidence=0.9),),
            )
        with Session(engine) as session:
            assert (
                session.scalar(
                    select(GraphClaimEvidenceRow.confidence).where(
                        GraphClaimEvidenceRow.series_id == series_id
                    )
                )
                == 0.8
            )
    finally:
        engine.dispose()
