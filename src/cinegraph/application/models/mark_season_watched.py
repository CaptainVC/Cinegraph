from dataclasses import dataclass
from uuid import UUID

from cinegraph.domain.enums.enum import WatchEventSource
from cinegraph.domain.models.watch_event.watch_event import WatchEvent
from cinegraph.domain.models.watch_state.profile_watch_state import ProfileWatchState


@dataclass(frozen=True, slots=True)
class MarkSeasonWatchedCommand:
    profile_id: UUID
    series_id: UUID
    season_id: UUID
    source: WatchEventSource = WatchEventSource.MANUAL


@dataclass(frozen=True, slots=True)
class MarkSeasonWatchedResult:
    watch_state: ProfileWatchState
    watch_events: tuple[WatchEvent, ...]
    was_already_watched: bool
