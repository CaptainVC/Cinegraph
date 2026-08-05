from dataclasses import dataclass
from uuid import UUID

from cinegraph.domain.models.transcript.transcript_segment import (
    TranscriptSegment,
)
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef
from cinegraph.domain.models.watch_state.profile_watch_state import (
    ProfileWatchState,
)


@dataclass(frozen=True, slots=True)
class GroundedAnswerQuery:
    question: str
    episode: EpisodeRef
    summary_source_document_id: UUID
    profile_watch_state: ProfileWatchState | None
    limit: int = 5


@dataclass(frozen=True, slots=True)
class ModelEvidence:
    segment_id: UUID
    episode: EpisodeRef
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True, slots=True)
class ModelRequest:
    question: str
    evidence: tuple[ModelEvidence, ...]


@dataclass(frozen=True, slots=True)
class ModelDraft:
    answer: str | None
    cited_segment_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class GroundedAnswerResult:
    answer: str | None
    citations: tuple[TranscriptSegment, ...]
    is_safe_refusal: bool
