from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from cinegraph.domain.enums.enum import SpoilerMode
from cinegraph.domain.models.access import CorpusAccessScope
from cinegraph.domain.models.watch_state import EpisodeRef


@dataclass(frozen=True, slots=True)
class AgentEvidenceCitation:
    citation_id: UUID
    kind: Literal["transcript", "graph"]
    episode: EpisodeRef
    start_ms: int
    end_ms: int
    segment_id: UUID | None = None
    claim_id: UUID | None = None
    evidence_id: UUID | None = None
    source_version_id: UUID | None = None
    transcript_chunk_id: UUID | None = None
    subject_display_name: str | None = None
    object_display_name: str | None = None
    predicate: str | None = None


@dataclass(frozen=True, slots=True)
class AgentEvidenceRequest:
    owner_profile_id: UUID
    series_id: UUID
    candidate_episodes: tuple[EpisodeRef, ...]
    permission_scope_revision: str
    spoiler_mode: SpoilerMode
    safe_through_episode_id: UUID | None
    citations: tuple[AgentEvidenceCitation, ...]


@dataclass(frozen=True, slots=True)
class AgentEvidenceExcerpt:
    citation_id: UUID
    kind: Literal["transcript", "graph"]
    episode: EpisodeRef
    source_version_id: UUID
    start_ms: int
    end_ms: int
    text: str
    score: float


@dataclass(frozen=True, slots=True)
class AgentEvidenceResult:
    excerpts: tuple[AgentEvidenceExcerpt, ...]


class AgentEvidenceNotFoundError(LookupError):
    """Selected evidence is not available in the current authorized scope."""


class AgentEvidenceReader(Protocol):
    def read(
        self,
        evidence_request: AgentEvidenceRequest,
        current_scope: CorpusAccessScope,
    ) -> AgentEvidenceResult: ...
