

from dataclasses import dataclass
from datetime import datetime

from cinegraph.domain.enums.enum import Language


@dataclass(frozen=True, slots=True)
class FetchedEpisodeSummary:
    page_title: str
    canonical_url: str
    revision_id: int
    revision_timestamp: datetime
    retrieved_at: datetime
    text: str
    language: Language
    attribution: str
