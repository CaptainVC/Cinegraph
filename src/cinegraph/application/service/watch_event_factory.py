from datetime import datetime
from uuid import UUID

from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.domain.enums.enum import WatchEventKind, WatchEventSource
from cinegraph.domain.models.watch_event.watch_event import WatchEvent
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef


def create_watch_events(
    *,
    profile_id: UUID,
    episodes: tuple[EpisodeRef, ...],
    kind: WatchEventKind,
    source: WatchEventSource,
    occurred_at: datetime,
) -> tuple[WatchEvent, ...]:
    return tuple(
        WatchEvent(
            event_id=IdentifierGenerator.new_id(),
            profile_id=profile_id,
            episode=episode,
            kind=kind,
            source=source,
            occurred_at=occurred_at,
        )
        for episode in episodes
    )
