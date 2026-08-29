from dataclasses import dataclass
from math import isfinite
from numbers import Real
from typing import Literal
from uuid import UUID

from cinegraph.common.error_messages import SeriesAgentErrorMessages
from cinegraph.common.graph_normalization import normalize_graph_identity, normalize_graph_predicate
from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.config.graph_claims import (
    GRAPH_CLAIM_EXTRACTION_REVISION,
    MAX_GRAPH_NAME_LENGTH,
    MAX_GRAPH_PREDICATE_LENGTH,
)
from cinegraph.config.series_agent import (
    MAX_SERIES_AGENT_ANSWER_LENGTH,
    MAX_SERIES_AGENT_CITATIONS,
    SERIES_AGENT_TOOL_NAMES,
)
from cinegraph.domain.enums.enum import GraphClaimPolarity, GraphEntityKind
from cinegraph.domain.models.watch_state import EpisodeRef

CitationKind = Literal["transcript", "graph"]


@dataclass(frozen=True, slots=True)
class SeriesAgentCitation:
    kind: CitationKind
    episode: EpisodeRef
    start_ms: int
    end_ms: int
    segment_id: UUID | None = None
    claim_id: UUID | None = None
    evidence_id: UUID | None = None
    source_version_id: UUID | None = None
    transcript_chunk_id: UUID | None = None
    subject_entity_id: UUID | None = None
    subject_kind: GraphEntityKind | None = None
    subject_display_name: str | None = None
    predicate: str | None = None
    object_entity_id: UUID | None = None
    object_kind: GraphEntityKind | None = None
    object_display_name: str | None = None
    polarity: GraphClaimPolarity | None = None
    hop_distance: int | None = None
    score: float | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"transcript", "graph"} or not isinstance(self.episode, EpisodeRef):
            raise ValueError(SeriesAgentErrorMessages.RESULT_CITATIONS_INVALID)
        if (
            isinstance(self.start_ms, bool)
            or not isinstance(self.start_ms, int)
            or isinstance(self.end_ms, bool)
            or not isinstance(self.end_ms, int)
            or self.start_ms < 0
            or self.end_ms <= self.start_ms
        ):
            raise ValueError(SeriesAgentErrorMessages.RESULT_CITATIONS_INVALID)
        if self.kind == "transcript" and (
            not isinstance(self.segment_id, UUID)
            or self.claim_id is not None
            or self.evidence_id is not None
            or any(
                value is not None
                for value in (
                    self.source_version_id, self.transcript_chunk_id,
                    self.subject_entity_id, self.subject_kind, self.subject_display_name,
                    self.predicate, self.object_entity_id, self.object_kind,
                    self.object_display_name, self.polarity, self.hop_distance, self.score,
                )
            )
        ):
            raise ValueError(SeriesAgentErrorMessages.RESULT_CITATIONS_INVALID)
        if self.kind == "graph" and (
            not isinstance(self.claim_id, UUID)
            or not isinstance(self.evidence_id, UUID)
            or self.segment_id is not None
        ):
            raise ValueError(SeriesAgentErrorMessages.RESULT_CITATIONS_INVALID)
        graph_metadata = (
            self.source_version_id,
            self.transcript_chunk_id,
            self.subject_entity_id,
            self.subject_kind,
            self.subject_display_name,
            self.predicate,
            self.object_entity_id,
            self.object_kind,
            self.object_display_name,
            self.polarity,
            self.hop_distance,
            self.score,
        )
        if self.kind == "graph" and any(
            value is not None
            for value in graph_metadata
        ):
            if not self._graph_metadata_valid():
                raise ValueError(SeriesAgentErrorMessages.RESULT_CITATIONS_INVALID)

    def _graph_metadata_valid(self) -> bool:
        if not all(
            value is not None
            for value in (
                self.source_version_id, self.transcript_chunk_id,
                self.subject_entity_id, self.subject_kind, self.subject_display_name,
                self.predicate, self.object_entity_id, self.object_kind,
                self.object_display_name, self.polarity, self.hop_distance, self.score,
            )
        ):
            return False
        assert self.source_version_id is not None and self.transcript_chunk_id is not None
        assert self.claim_id is not None and self.evidence_id is not None
        assert self.subject_entity_id is not None and self.subject_kind is not None
        assert self.subject_display_name is not None and self.predicate is not None
        assert self.object_entity_id is not None and self.object_kind is not None
        assert self.object_display_name is not None and self.polarity is not None
        assert self.hop_distance is not None and self.score is not None
        try:
            return (
                self.subject_display_name.strip() == self.subject_display_name
                and bool(self.subject_display_name)
                and len(self.subject_display_name) <= MAX_GRAPH_NAME_LENGTH
                and self.object_display_name.strip() == self.object_display_name
                and bool(self.object_display_name)
                and len(self.object_display_name) <= MAX_GRAPH_NAME_LENGTH
                and normalize_graph_predicate(self.predicate) == self.predicate
                and len(self.predicate) <= MAX_GRAPH_PREDICATE_LENGTH
                and isinstance(self.hop_distance, int)
                and not isinstance(self.hop_distance, bool)
                and self.hop_distance >= 1
                and isinstance(self.score, Real)
                and not isinstance(self.score, bool)
                and isfinite(float(self.score))
                and 0 <= self.score <= 1
                and self.subject_entity_id == IdentifierGenerator.graph_entity_id(
                    self.episode.series_id, self.subject_kind,
                    normalize_graph_identity(self.subject_display_name),
                )
                and self.object_entity_id == IdentifierGenerator.graph_entity_id(
                    self.episode.series_id, self.object_kind,
                    normalize_graph_identity(self.object_display_name),
                )
                and self.claim_id == IdentifierGenerator.graph_claim_id(
                    GRAPH_CLAIM_EXTRACTION_REVISION, self.episode.series_id,
                    self.subject_entity_id, self.predicate, self.object_entity_id, self.polarity,
                )
                and self.evidence_id == IdentifierGenerator.graph_evidence_id(
                    self.claim_id, self.source_version_id, self.transcript_chunk_id,
                )
            )
        except (TypeError, ValueError):
            return False

    @property
    def citation_id(self) -> UUID:
        identifier = self.segment_id or self.evidence_id
        if identifier is None:
            raise ValueError(SeriesAgentErrorMessages.RESULT_CITATIONS_INVALID)
        return identifier


@dataclass(frozen=True, slots=True)
class SeriesAgentResult:
    answer: str | None
    is_safe_refusal: bool
    citations: tuple[SeriesAgentCitation, ...] = ()
    used_tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.is_safe_refusal, bool)
            or not isinstance(self.citations, tuple)
            or not isinstance(self.used_tools, tuple)
        ):
            raise ValueError(SeriesAgentErrorMessages.RESULT_IMMUTABLE)
        if self.is_safe_refusal and (self.answer is not None or self.citations):
            raise ValueError(SeriesAgentErrorMessages.RESULT_REFUSAL)
        if not self.is_safe_refusal and (
            not isinstance(self.answer, str)
            or not self.answer.strip()
            or self.answer.strip() != self.answer
            or len(self.answer) > MAX_SERIES_AGENT_ANSWER_LENGTH
            or not self.citations
        ):
            raise ValueError(SeriesAgentErrorMessages.RESULT_GROUNDED_REQUIRED)
        if any(not isinstance(item, SeriesAgentCitation) for item in self.citations) or any(
            not isinstance(item, str) or not item.strip() or item.strip() != item
            for item in self.used_tools
        ):
            raise ValueError(SeriesAgentErrorMessages.RESULT_CITATIONS_INVALID)
        if (
            len(self.citations) > MAX_SERIES_AGENT_CITATIONS
            or len(set(self.used_tools)) != len(self.used_tools)
            or not set(self.used_tools).issubset(SERIES_AGENT_TOOL_NAMES)
        ):
            raise ValueError(SeriesAgentErrorMessages.RESULT_CITATIONS_INVALID)
        if len(
            {
                (item.kind, item.segment_id, item.claim_id, item.evidence_id)
                for item in self.citations
            }
        ) != len(self.citations):
            raise ValueError(SeriesAgentErrorMessages.RESULT_DUPLICATE_CITATIONS)
