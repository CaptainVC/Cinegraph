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
class GetVisibleEpisodeContextQuery:
    episode: EpisodeRef
    summary_source_document_id: UUID
    profile_watch_state: ProfileWatchState | None
    corpus_access_scope: CorpusAccessScope


@dataclass(frozen=True, slots=True)
class GetVisibleEpisodeContextResult:
    summary: EpisodeSummaryDocument | None
    transcript_segments: tuple[TranscriptSegment, ...]
    safe_until_ms: int | None
    summary_is_model_context_only: bool
