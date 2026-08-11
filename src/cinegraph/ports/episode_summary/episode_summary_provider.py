from typing import Protocol

from cinegraph.domain.enums.enum import Language
from cinegraph.ports.dto.fetched_episode_summary import FetchedEpisodeSummary


class EpisodeSummaryProvider(Protocol):
        # Fetch one localized episode summary with source and retrieval metadata.
    def fetch(
            self,
            *,
            page_title: str,
            language: Language,
    ) -> FetchedEpisodeSummary: ...
