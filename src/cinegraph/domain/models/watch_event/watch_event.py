from dataclasses import dataclass

from datetime import datetime
from uuid import UUID

from cinegraph.common.error_messages import WatchErrorMessages
from cinegraph.domain.enums.enum import WatchEventKind, WatchEventSource
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef


@dataclass(frozen=True, slots=True)
class WatchEvent:
    event_id: UUID
    profile_id: UUID
    episode: EpisodeRef
    kind: WatchEventKind
    source: WatchEventSource
    occurred_at: datetime

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None:
            raise InvalidModelError(
                WatchErrorMessages.WATCH_EVENT_TIMESTAMP_MUST_BE_TIMEZONE_AWARE
            )