from typing import Protocol
from uuid import UUID

from cinegraph.domain.models.watch_event.watch_event import WatchEvent
from cinegraph.domain.models.watch_state.profile_watch_state import ProfileWatchState


class WatchProgressRepository(Protocol):

        # Return the persisted watch state for a profile, if available.
    def get(self, profile_id: UUID) -> ProfileWatchState | None: ...

        # Persist a version-checked profile state and its watch events.
    def persist_state_change(
            self,
            watch_state: ProfileWatchState,
            watch_events: tuple[WatchEvent, ...],
            *,
            expected_version: int,
    ) -> None: ...
