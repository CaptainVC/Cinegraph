from typing import Protocol
from uuid import UUID

from cinegraph.domain.models.watch_event.watch_event import WatchEvent
from cinegraph.domain.models.watch_state.profile_watch_state import ProfileWatchState


class WatchProgressRepository(Protocol):

    def get(self, profile_id: UUID) -> ProfileWatchState | None: ...

    def persist_state_change(
            self,
            watch_state: ProfileWatchState,
            watch_events: tuple[WatchEvent, ...],
            *,
            expected_version: int,
    ) -> None: ...
