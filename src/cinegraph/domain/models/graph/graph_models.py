from dataclasses import dataclass
from math import isfinite
from uuid import UUID

from cinegraph.common.error_messages import GraphErrorMessages
from cinegraph.common.graph_normalization import normalize_graph_identity, normalize_graph_predicate
from cinegraph.config.graph_claims import (
    GRAPH_CLAIM_EXTRACTION_REVISION,
    MAX_GRAPH_ALIASES,
    MAX_GRAPH_NAME_LENGTH,
    MAX_GRAPH_PREDICATE_LENGTH,
)
from cinegraph.config.transcript_chunking import TRANSCRIPT_INDEX_REVISION
from cinegraph.domain.enums.enum import (
    GraphClaimPolarity,
    GraphEntityKind,
    RightsStatus,
    SourceReviewStatus,
    SourceVersionStatus,
)
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.source.review_status import is_source_version_approved
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef


def _confidence(value: float) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or not 0 <= value <= 1
    ):
        raise InvalidModelError(GraphErrorMessages.CONFIDENCE_INVALID)


@dataclass(frozen=True, slots=True)
class GraphEntity:
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
        ):
            raise InvalidModelError(GraphErrorMessages.ENTITY_FIELDS_INVALID)
        if (
            not isinstance(self.display_name, str)
            or not 0 < len(self.display_name) <= MAX_GRAPH_NAME_LENGTH
            or self.display_name.strip() != self.display_name
        ):
            raise InvalidModelError(GraphErrorMessages.ENTITY_NAME_INVALID)
        try:
            normalized = normalize_graph_identity(self.normalized_key)
        except ValueError as error:
            raise InvalidModelError(GraphErrorMessages.ENTITY_KEY_INVALID) from error
        if normalized != self.normalized_key or len(normalized) > MAX_GRAPH_NAME_LENGTH:
            raise InvalidModelError(GraphErrorMessages.ENTITY_KEY_INVALID)
        if normalize_graph_identity(self.display_name) != self.normalized_key:
            raise InvalidModelError(GraphErrorMessages.ENTITY_KEY_INVALID)
        if (
            not isinstance(self.aliases, tuple)
            or not self.aliases
            or len(self.aliases) > MAX_GRAPH_ALIASES
        ):
            raise InvalidModelError(GraphErrorMessages.ENTITY_ALIASES_INVALID)
        if any(
            not isinstance(alias, str)
            or not alias
            or alias.strip() != alias
            or len(alias) > MAX_GRAPH_NAME_LENGTH
            for alias in self.aliases
        ):
            raise InvalidModelError(GraphErrorMessages.ENTITY_ALIASES_INVALID)
        alias_keys = tuple(normalize_graph_identity(alias) for alias in self.aliases)
        if len(set(alias_keys)) != len(alias_keys) or self.normalized_key not in alias_keys:
            raise InvalidModelError(GraphErrorMessages.ENTITY_ALIASES_INVALID)


@dataclass(frozen=True, slots=True)
class GraphClaim:
    claim_id: UUID
    series_id: UUID
    subject_entity_id: UUID
    predicate: str
    object_entity_id: UUID
    polarity: GraphClaimPolarity
    extraction_revision: str = GRAPH_CLAIM_EXTRACTION_REVISION

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, UUID)
            for value in (
                self.claim_id,
                self.series_id,
                self.subject_entity_id,
                self.object_entity_id,
            )
        ):
            raise InvalidModelError(GraphErrorMessages.CLAIM_FIELDS_INVALID)
        if (
            not isinstance(self.polarity, GraphClaimPolarity)
            or self.extraction_revision != GRAPH_CLAIM_EXTRACTION_REVISION
        ):
            raise InvalidModelError(GraphErrorMessages.CLAIM_FIELDS_INVALID)
        try:
            predicate = normalize_graph_predicate(self.predicate)
        except ValueError as error:
            raise InvalidModelError(GraphErrorMessages.CLAIM_PREDICATE_INVALID) from error
        if predicate != self.predicate or len(predicate) > MAX_GRAPH_PREDICATE_LENGTH:
            raise InvalidModelError(GraphErrorMessages.CLAIM_PREDICATE_INVALID)


@dataclass(frozen=True, slots=True)
class GraphClaimEvidence:
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
        if not all(
            isinstance(value, UUID)
            for value in (
                self.evidence_id,
                self.claim_id,
                self.source_version_id,
                self.transcript_chunk_id,
            )
        ) or not isinstance(self.episode, EpisodeRef):
            raise InvalidModelError(GraphErrorMessages.EVIDENCE_FIELDS_INVALID)
        if (
            isinstance(self.start_ms, bool)
            or not isinstance(self.start_ms, int)
            or isinstance(self.end_ms, bool)
            or not isinstance(self.end_ms, int)
            or self.start_ms < 0
            or self.end_ms <= self.start_ms
        ):
            raise InvalidModelError(GraphErrorMessages.EVIDENCE_FIELDS_INVALID)
        _confidence(self.confidence)
        if (
            self.transcript_index_revision != TRANSCRIPT_INDEX_REVISION
            or self.extraction_revision != GRAPH_CLAIM_EXTRACTION_REVISION
        ):
            raise InvalidModelError(GraphErrorMessages.EVIDENCE_FIELDS_INVALID)
        if (
            self.rights_status is not RightsStatus.ALLOWED
            or self.source_status is not SourceVersionStatus.ACTIVE
            or not is_source_version_approved(self.review_status)
        ):
            raise InvalidModelError(GraphErrorMessages.SOURCE_NOT_GOVERNED)
