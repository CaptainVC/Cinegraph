from dataclasses import replace
from uuid import uuid4

import pytest

from cinegraph.application.models.graph_rag import GraphRagResult
from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.config.graph_claims import GRAPH_CLAIM_EXTRACTION_REVISION
from cinegraph.config.graph_rag import MAX_GRAPH_RAG_CLAIMS, MAX_GRAPH_RAG_EVIDENCE_PER_CLAIM
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
from cinegraph.ports.graph.graph_claim_read_models import (
    GraphRagReadClaim,
    GraphRagReadEntity,
    GraphRagReadEvidence,
)


def _entity(series_id, name: str) -> GraphRagReadEntity:
    key = name.casefold()
    return GraphRagReadEntity(
        IdentifierGenerator.graph_entity_id(series_id, GraphEntityKind.CHARACTER, key),
        series_id,
        GraphEntityKind.CHARACTER,
        key,
        name,
        (name,),
    )


def _claim(evidence_count: int = 1) -> GraphRagReadClaim:
    series_id = uuid4()
    subject, object_ = _entity(series_id, "Alex"), _entity(series_id, "Sam")
    claim_id = IdentifierGenerator.graph_claim_id(
        GRAPH_CLAIM_EXTRACTION_REVISION,
        series_id,
        subject.entity_id,
        "knows",
        object_.entity_id,
        GraphClaimPolarity.ASSERTED,
    )
    episode = EpisodeRef(series_id, uuid4(), uuid4(), EpisodePosition(1, 1))
    evidence = []
    for _ in range(evidence_count):
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
                0.8,
                TRANSCRIPT_INDEX_REVISION,
                GRAPH_CLAIM_EXTRACTION_REVISION,
                RightsStatus.ALLOWED,
                SourceVersionStatus.ACTIVE,
                SourceReviewStatus.AUTOMATED_REVIEWED,
            )
        )
    return GraphRagReadClaim(
        claim_id,
        series_id,
        subject,
        "knows",
        object_,
        GraphClaimPolarity.ASSERTED,
        1,
        0.8,
        tuple(evidence),
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"entity_id": uuid4()},
        {"normalized_key": "wrong"},
        {"display_name": " Alex"},
        {"aliases": ()},
        {"aliases": ("Alex", "Ａlex")},
        {"aliases": ("Sam",)},
    ],
)
def test_read_entity_rejects_unstable_or_noncanonical_identity(
    changes: dict[str, object],
) -> None:
    with pytest.raises(InvalidModelError):
        replace(_entity(uuid4(), "Alex"), **changes)


def test_read_evidence_rejects_unstable_id_and_ungoverned_metadata() -> None:
    claim = _claim()
    evidence = claim.evidence[0]

    with pytest.raises(InvalidModelError):
        replace(evidence, evidence_id=uuid4())
    for changes in (
        {"rights_status": RightsStatus.RESTRICTED},
        {"review_status": SourceReviewStatus.PENDING},
        {"transcript_index_revision": "old"},
    ):
        with pytest.raises(InvalidModelError):
            replace(evidence, **changes)


def test_read_claim_rejects_unstable_semantics_duplicates_and_hard_evidence_overflow() -> None:
    claim = _claim()

    for changes in (
        {"claim_id": uuid4()},
        {"predicate": "Not Normalized"},
        {"hop_distance": 0},
        {"evidence": claim.evidence + claim.evidence},
    ):
        with pytest.raises(InvalidModelError):
            replace(claim, **changes)
    with pytest.raises(InvalidModelError):
        _claim(MAX_GRAPH_RAG_EVIDENCE_PER_CLAIM + 1)


def test_result_rejects_duplicate_and_hard_claim_overflow() -> None:
    claim = _claim()
    with pytest.raises(InvalidModelError):
        GraphRagResult((claim, claim))
    with pytest.raises(InvalidModelError):
        GraphRagResult(tuple(_claim() for _ in range(MAX_GRAPH_RAG_CLAIMS + 1)))
