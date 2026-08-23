from typing import Protocol
from uuid import UUID

from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef
from cinegraph.ports.dto.fetched_series_metadata import FetchedSeriesMetadata


class SeriesMetadataProvider(Protocol):
    def fetch(
        self,
        *,
        provider_show_id: int,
        expected_title: str,
        series_id: UUID,
        episodes: tuple[EpisodeRef, ...],
    ) -> FetchedSeriesMetadata: ...
