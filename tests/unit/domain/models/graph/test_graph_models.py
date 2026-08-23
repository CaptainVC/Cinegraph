from dataclasses import replace
from math import inf, nan
from uuid import uuid4

import pytest

from cinegraph.common.error_messages import GraphErrorMessages
from cinegraph.common.graph_normalization import normalize_graph_identity
from cinegraph.config.graph_claims import GRAPH_CLAIM_EXTRACTION_REVISION
from cinegraph.config.transcript_chunking import TRANSCRIPT_INDEX_REVISION
from cinegraph.domain.enums.enum import (
    GraphClaimPolarity,
    GraphEntityKind,
    RightsStatus,
    SourceReviewStatus,
    SourceVersionStatus,
)
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.graph.graph_models import GraphClaim, GraphClaimEvidence, GraphEntity
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodePosition, EpisodeRef


def _entity(name: str = "Alex", aliases: tuple[str, ...] = ("Alex",)) -> GraphEntity:
    return GraphEntity(
        uuid4(),
        uuid4(),
        GraphEntityKind.CHARACTER,
        normalize_graph_identity(name),
        name,
        aliases if aliases != ("Alex",) or name == "Alex" else (name,),
    )


def _claim(subject: GraphEntity | None = None, object_entity: GraphEntity | None = None) -> GraphClaim:
    subject = subject or _entity("Alex")
    object_entity = object_entity or _entity("Sam")
    return GraphClaim(
        uuid4(),
        subject.series_id,
        subject.entity_id,
        "knows",
        object_entity.entity_id,
        GraphClaimPolarity.ASSERTED,
    )


def _episode() -> EpisodeRef:
    return EpisodeRef(uuid4(), uuid4(), uuid4(), EpisodePosition(1, 1))


def _evidence(**changes: object) -> GraphClaimEvidence:
    subject, object_entity = _entity("Alex"), _entity("Sam")
    claim = _claim(subject, object_entity)
    values: dict[str, object] = {
        "evidence_id": uuid4(),
        "claim_id": claim.claim_id,
        "source_version_id": uuid4(),
        "transcript_chunk_id": uuid4(),
        "episode": _episode(),
        "start_ms": 0,
        "end_ms": 1000,
        "confidence": 0.75,
        "transcript_index_revision": TRANSCRIPT_INDEX_REVISION,
        "extraction_revision": GRAPH_CLAIM_EXTRACTION_REVISION,
        "rights_status": RightsStatus.ALLOWED,
        "source_status": SourceVersionStatus.ACTIVE,
        "review_status": SourceReviewStatus.AUTOMATED_REVIEWED,
    }
    values.update(changes)
    return GraphClaimEvidence(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [
        {"display_name": " Alex"},
        {"display_name": ""},
        {"display_name": "x" * 257},
        {"normalized_key": "wrong-key"},
        {"aliases": ()},
        {"aliases": ("Alex", " alex ")},
        {"aliases": ("Sam",)},
        {"aliases": ("x" * 257,)},
        {"aliases": tuple(f"alias-{index}" for index in range(33))},
    ],
)
def test_graph_entity_rejects_identity_and_alias_boundaries(changes: dict[str, object]) -> None:
    with pytest.raises(InvalidModelError):
        replace(_entity(), **changes)


def test_graph_entity_preserves_immutable_series_scoped_identity() -> None:
    entity = _entity("Ａlex", ("Ａlex",))
    assert entity.normalized_key == "alex"
    assert entity.display_name == "Ａlex"
    assert entity.aliases == ("Ａlex",)


@pytest.mark.parametrize("predicate", ["Knows", "has/slash", "has__gap", "", " "])
def test_graph_claim_rejects_non_normalized_predicates(predicate: str) -> None:
    subject, object_entity = _entity("Alex"), _entity("Sam")
    with pytest.raises(InvalidModelError, match=GraphErrorMessages.CLAIM_PREDICATE_INVALID):
        replace(_claim(subject, object_entity), predicate=predicate)


def test_graph_claim_rejects_revision_changes() -> None:
    with pytest.raises(InvalidModelError, match=GraphErrorMessages.CLAIM_FIELDS_INVALID):
        replace(_claim(), extraction_revision="old")


def test_graph_claim_allows_self_edges_for_phase_30_conflict_ranking() -> None:
    entity = _entity()
    claim = _claim(entity, entity)
    assert claim.subject_entity_id == claim.object_entity_id


@pytest.mark.parametrize(
    "changes",
    [
        {"confidence": True},
        {"confidence": inf},
        {"confidence": nan},
        {"confidence": -0.01},
        {"confidence": 1.01},
        {"start_ms": True},
        {"end_ms": 0},
        {"transcript_index_revision": "old"},
        {"extraction_revision": "old"},
        {"rights_status": RightsStatus.RESTRICTED},
        {"source_status": SourceVersionStatus.RETIRED},
        {"review_status": SourceReviewStatus.PENDING},
    ],
)
def test_graph_evidence_rejects_confidence_timing_revision_and_governance(changes: dict[str, object]) -> None:
    with pytest.raises(InvalidModelError):
        replace(_evidence(), **changes)
