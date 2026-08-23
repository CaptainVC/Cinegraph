from dataclasses import dataclass
from math import isfinite
from uuid import UUID

from cinegraph.common.error_messages import GraphErrorMessages
from cinegraph.domain.enums.enum import GraphClaimPolarity, GraphEntityKind
from cinegraph.domain.exceptions.errors import InvalidModelError


@dataclass(frozen=True, slots=True)
class ExtractedEntityReference:
    kind: GraphEntityKind
    name: str
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.kind, GraphEntityKind)
            or not isinstance(self.name, str)
            or not self.name.strip()
            or self.name.strip() != self.name
            or not isinstance(self.aliases, tuple)
        ):
            raise InvalidModelError(GraphErrorMessages.ENTITY_FIELDS_INVALID)


@dataclass(frozen=True, slots=True)
class ExtractedGraphClaim:
    subject: ExtractedEntityReference
    predicate: str
    object: ExtractedEntityReference
    polarity: GraphClaimPolarity
    confidence: float
    evidence_chunk_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.subject, ExtractedEntityReference)
            or not isinstance(self.object, ExtractedEntityReference)
            or not isinstance(self.polarity, GraphClaimPolarity)
            or not isinstance(self.predicate, str)
            or not self.predicate.strip()
            or not isinstance(self.evidence_chunk_ids, tuple)
            or not self.evidence_chunk_ids
        ):
            raise InvalidModelError(GraphErrorMessages.CLAIM_FIELDS_INVALID)
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not isfinite(self.confidence)
            or not 0 <= self.confidence <= 1
        ):
            raise InvalidModelError(GraphErrorMessages.CONFIDENCE_INVALID)
        if any(not isinstance(item, UUID) for item in self.evidence_chunk_ids) or len(
            set(self.evidence_chunk_ids)
        ) != len(self.evidence_chunk_ids):
            raise InvalidModelError(GraphErrorMessages.UNKNOWN_EVIDENCE)
