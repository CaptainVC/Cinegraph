from dataclasses import dataclass
from math import isfinite
from numbers import Real
from uuid import UUID

from cinegraph.common.error_messages import GraphRagErrorMessages
from cinegraph.common.graph_normalization import normalize_graph_identity, normalize_graph_predicate
from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.config.graph_claims import (
    GRAPH_CLAIM_EXTRACTION_REVISION,
    MAX_GRAPH_ALIASES,
    MAX_GRAPH_NAME_LENGTH,
    MAX_GRAPH_PREDICATE_LENGTH,
)
from cinegraph.config.graph_rag import MAX_GRAPH_RAG_EVIDENCE_PER_CLAIM
from cinegraph.config.transcript_chunking import TRANSCRIPT_INDEX_REVISION
from cinegraph.domain.enums.enum import (
    GraphClaimPolarity,
    GraphEntityKind,
    RightsStatus,
    SourceReviewStatus,
    SourceVersionStatus,
)
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef


def _identity_or_none(value: str) -> str | None:
    try:
        return normalize_graph_identity(value)
    except ValueError:
        return None


def _predicate_or_none(value: str) -> str | None:
    try:
        return normalize_graph_predicate(value)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class GraphRagReadEntity:
    entity_id: UUID
    series_id: UUID
    kind: GraphEntityKind
    normalized_key: str
    display_name: str
    aliases: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.entity_id, UUID)
            or not isinstance(self.series_id, UUID)
            or not isinstance(self.kind, GraphEntityKind)
            or not isinstance(self.normalized_key, str)
            or len(self.normalized_key) > MAX_GRAPH_NAME_LENGTH
            or not isinstance(self.display_name, str)
            or not self.display_name.strip()
            or self.display_name.strip() != self.display_name
            or len(self.display_name) > MAX_GRAPH_NAME_LENGTH
            or _identity_or_none(self.display_name) != self.normalized_key
            or not isinstance(self.aliases, tuple)
            or not self.aliases
            or len(self.aliases) > MAX_GRAPH_ALIASES
            or any(
                not isinstance(item, str)
                or not item.strip()
                or item.strip() != item
                or len(item) > MAX_GRAPH_NAME_LENGTH
                for item in self.aliases
            )
            or len({_identity_or_none(item) for item in self.aliases}) != len(self.aliases)
            or self.normalized_key not in {_identity_or_none(item) for item in self.aliases}
            or self.entity_id
            != IdentifierGenerator.graph_entity_id(self.series_id, self.kind, self.normalized_key)
        ):
            raise InvalidModelError(GraphRagErrorMessages.RESULT_INVALID)


@dataclass(frozen=True, slots=True)
class GraphRagReadEvidence:
    evidence_id: UUID
    claim_id: UUID
    source_version_id: UUID
    transcript_chunk_id: UUID
    episode: EpisodeRef
    start_ms: int
    end_ms: int
    confidence: float
    transcript_index_revision: str
    extraction_revision: str
    rights_status: RightsStatus
    source_status: SourceVersionStatus
    review_status: SourceReviewStatus

    def __post_init__(self) -> None:
        if (
            not all(
                isinstance(item, UUID)
                for item in (
                    self.evidence_id,
                    self.claim_id,
                    self.source_version_id,
                    self.transcript_chunk_id,
                )
            )
            or not isinstance(self.episode, EpisodeRef)
            or isinstance(self.start_ms, bool)
            or not isinstance(self.start_ms, int)
            or isinstance(self.end_ms, bool)
            or not isinstance(self.end_ms, int)
            or self.start_ms < 0
            or self.end_ms <= self.start_ms
            or isinstance(self.confidence, bool)
            or not isinstance(self.confidence, Real)
            or not isfinite(self.confidence)
            or not 0 <= self.confidence <= 1
            or self.transcript_index_revision != TRANSCRIPT_INDEX_REVISION
            or self.extraction_revision != GRAPH_CLAIM_EXTRACTION_REVISION
            or self.rights_status is not RightsStatus.ALLOWED
            or self.source_status is not SourceVersionStatus.ACTIVE
            or self.review_status
            not in {
                SourceReviewStatus.AUTOMATED_REVIEWED,
                SourceReviewStatus.HYBRID_REVIEWED,
                SourceReviewStatus.REVIEWED,
            }
            or self.evidence_id
            != IdentifierGenerator.graph_evidence_id(
                self.claim_id,
                self.source_version_id,
                self.transcript_chunk_id,
            )
        ):
            raise InvalidModelError(GraphRagErrorMessages.RESULT_SCOPE_INVALID)


@dataclass(frozen=True, slots=True)
class GraphRagReadClaim:
    claim_id: UUID
    series_id: UUID
    subject: GraphRagReadEntity
    predicate: str
    object: GraphRagReadEntity
    polarity: GraphClaimPolarity
    hop_distance: int
    score: float
    evidence: tuple[GraphRagReadEvidence, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.claim_id, UUID)
            or not isinstance(self.series_id, UUID)
            or not isinstance(self.subject, GraphRagReadEntity)
            or not isinstance(self.object, GraphRagReadEntity)
            or self.subject.series_id != self.series_id
            or self.object.series_id != self.series_id
            or not isinstance(self.predicate, str)
            or len(self.predicate) > MAX_GRAPH_PREDICATE_LENGTH
            or _predicate_or_none(self.predicate) != self.predicate
            or not isinstance(self.polarity, GraphClaimPolarity)
            or isinstance(self.hop_distance, bool)
            or not isinstance(self.hop_distance, int)
            or self.hop_distance < 1
            or isinstance(self.score, bool)
            or not isinstance(self.score, Real)
            or not isfinite(self.score)
            or not isinstance(self.evidence, tuple)
            or not self.evidence
            or len(self.evidence) > MAX_GRAPH_RAG_EVIDENCE_PER_CLAIM
            or len({item.evidence_id for item in self.evidence}) != len(self.evidence)
            or any(
                item.claim_id != self.claim_id
                or item.evidence_id
                != IdentifierGenerator.graph_evidence_id(
                    item.claim_id, item.source_version_id, item.transcript_chunk_id
                )
                for item in self.evidence
            )
            or self.claim_id
            != IdentifierGenerator.graph_claim_id(
                GRAPH_CLAIM_EXTRACTION_REVISION,
                self.series_id,
                self.subject.entity_id,
                self.predicate,
                self.object.entity_id,
                self.polarity,
            )
        ):
            raise InvalidModelError(GraphRagErrorMessages.RESULT_INVALID)
