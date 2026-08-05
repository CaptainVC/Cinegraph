from dataclasses import dataclass
from uuid import UUID

from cinegraph.domain.models.episode_summary.episode_summary_document import (
    EpisodeSummaryDocument,
)
from cinegraph.domain.models.watch_state.profile_watch_state import (
    ProfileWatchState,
)


@dataclass(frozen=True, slots=True)
class GetVisibleEpisodeSummaryQuery:
    source_document_id: UUID
    profile_watch_state: ProfileWatchState | None


@dataclass(frozen=True, slots=True)
class GetVisibleEpisodeSummaryResult:
    summary: EpisodeSummaryDocument | None
    safe_until_ms: int | None = None
    is_model_context_only: bool = False