from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from cinegraph.common.error_messages import SeriesAgentErrorMessages
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
        ):
            raise ValueError(SeriesAgentErrorMessages.RESULT_CITATIONS_INVALID)
        if self.kind == "graph" and (
            not isinstance(self.claim_id, UUID)
            or not isinstance(self.evidence_id, UUID)
            or self.segment_id is not None
        ):
            raise ValueError(SeriesAgentErrorMessages.RESULT_CITATIONS_INVALID)


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
            or not self.citations
        ):
            raise ValueError(SeriesAgentErrorMessages.RESULT_GROUNDED_REQUIRED)
        if any(not isinstance(item, SeriesAgentCitation) for item in self.citations) or any(
            not isinstance(item, str) or not item.strip() or item.strip() != item
            for item in self.used_tools
        ):
            raise ValueError(SeriesAgentErrorMessages.RESULT_CITATIONS_INVALID)
        if len(
            {
                (item.kind, item.segment_id, item.claim_id, item.evidence_id)
                for item in self.citations
            }
        ) != len(self.citations):
            raise ValueError(SeriesAgentErrorMessages.RESULT_DUPLICATE_CITATIONS)
