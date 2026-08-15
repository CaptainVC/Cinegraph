from dataclasses import dataclass
from uuid import UUID

from cinegraph.application.models.search_visible_episode_segments import (
    RankedTranscriptSegment,
)
from cinegraph.domain.models.watch_state.profile_watch_state import (
    ProfileWatchState,
)


@dataclass(frozen=True, slots=True)
class SearchVisibleSeasonSegmentsQuery:
    query: str
    series_id: UUID
    season_id: UUID
    profile_watch_state: ProfileWatchState | None
    limit: int = 10


@dataclass(frozen=True, slots=True)
class SearchVisibleSeasonSegmentsResult:
    matches: tuple[RankedTranscriptSegment, ...]
