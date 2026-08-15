from typing import TypedDict
from uuid import UUID

from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef
from cinegraph.domain.models.watch_state.profile_watch_state import ProfileWatchState


class AgentRuntimeContext(TypedDict):
    episode: EpisodeRef
    summary_source_document_id: UUID
    profile_watch_state: ProfileWatchState | None
