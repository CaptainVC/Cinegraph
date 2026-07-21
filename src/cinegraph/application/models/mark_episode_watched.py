from dataclasses import dataclass
from uuid import UUID

from cinegraph.domain.enums.enum import WatchEventSource
from cinegraph.domain.models.watch_event.watch_event import WatchEvent
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef
from cinegraph.domain.models.watch_state.profile_watch_state import ProfileWatchState


@dataclass(frozen=True, slots=True)
class MarkEpisodeWatchedCommand:
    profile_id: UUID
    episode: EpisodeRef
    source: WatchEventSource = WatchEventSource.MANUAL

@dataclass(frozen=True, slots=True)
class MarkEpisodeWatchedResult:
    watch_state: ProfileWatchState
    watch_event: WatchEvent | None
    was_already_watched: bool
