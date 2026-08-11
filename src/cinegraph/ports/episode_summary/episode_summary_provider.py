from typing import Protocol

from cinegraph.domain.enums.enum import Language
from cinegraph.ports.dto.fetched_episode_summary import FetchedEpisodeSummary


class EpisodeSummaryProvider(Protocol):
    # Processes the supplied fetch values.
    def fetch(
            self,
            *,
            page_title: str,
            language: Language,
    ) -> FetchedEpisodeSummary: ...
