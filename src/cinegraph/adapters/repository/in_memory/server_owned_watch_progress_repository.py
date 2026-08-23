"""Process-local relaxed baseline; persisted watch progress can replace it later."""

from uuid import UUID

from cinegraph.common.error_messages import AgentJobErrorMessages
from cinegraph.domain.enums.enum import SpoilerMode
from cinegraph.domain.models.watch_event import WatchEvent
from cinegraph.domain.models.watch_state import ProfileWatchState


class ServerOwnedWatchProgressRepository:
    def get(self, profile_id: UUID) -> ProfileWatchState:
        return ProfileWatchState(
            profile_id=profile_id,
            profile_name="API session",
            spoiler_mode=SpoilerMode.RELAXED,
        )

    def persist_state_change(
        self,
        watch_state: ProfileWatchState,
        watch_events: tuple[WatchEvent, ...],
        *,
        expected_version: int,
    ) -> None:
        raise RuntimeError(AgentJobErrorMessages.WATCH_STATE_READ_ONLY)
