from dataclasses import dataclass
from uuid import UUID

from cinegraph.domain.models.access import CorpusAccessScope
from cinegraph.domain.models.episode_summary.episode_summary_document import (
    EpisodeSummaryDocument,
)
from cinegraph.domain.models.transcript.transcript_segment import (
    TranscriptSegment,
)
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef
from cinegraph.domain.models.watch_state.profile_watch_state import (
    ProfileWatchState,
)


@dataclass(frozen=True, slots=True)
class SearchVisibleEpisodeSegmentsQuery:
    query: str
    episode: EpisodeRef
    summary_source_document_id: UUID
    profile_watch_state: ProfileWatchState | None
    corpus_access_scope: CorpusAccessScope
    limit: int = 5


@dataclass(frozen=True, slots=True)
class RankedTranscriptSegment:
    segment: TranscriptSegment
    score: float


@dataclass(frozen=True, slots=True)
class SearchVisibleEpisodeSegmentsResult:
    summary: EpisodeSummaryDocument | None
    summary_is_model_context_only: bool
    safe_until_ms: int | None
    matches: tuple[RankedTranscriptSegment, ...]
