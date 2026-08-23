from dataclasses import dataclass
from datetime import datetime

from cinegraph.domain.models.series_metadata import (
    ArtworkAsset,
    CreditedPerson,
    EpisodeCastMetadata,
)


@dataclass(frozen=True, slots=True)
class FetchedSeriesMetadata:
    provider_name: str
    provider_show_id: int
    title: str
    canonical_url: str
    poster: ArtworkAsset | None
    regular_cast: tuple[CreditedPerson, ...]
    episodes: tuple[EpisodeCastMetadata, ...]
    retrieved_at: datetime
    attribution: str
    license_name: str
    license_url: str
