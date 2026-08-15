from typing import Protocol
from uuid import UUID

from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef


class SeasonEpisodeCatalog(Protocol):
    # Return the canonical episode references for a series season, if catalogued.
    def get_episode_refs(
        self,
        series_id: UUID,
        season_id: UUID,
    ) -> tuple[EpisodeRef, ...] | None: ...
