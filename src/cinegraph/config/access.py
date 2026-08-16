from dataclasses import dataclass
from uuid import UUID

from cinegraph.domain.enums.enum import CorpusAccessMode
from cinegraph.domain.models.access import CorpusAccessScope, CorpusSeasonAccess


@dataclass(frozen=True, slots=True)
class GuestAccessConfiguration:
    series_id: UUID
    season_numbers: frozenset[int]
    revision: str


DEFAULT_GUEST_ACCESS_CONFIGURATION = GuestAccessConfiguration(
    series_id=UUID("00000000-0000-0000-0000-000000000011"),
    season_numbers=frozenset({1, 2}),
    revision="guest-modern-family-s01-s02-v1",
)

DEFAULT_GUEST_CORPUS_ACCESS_SCOPE = CorpusAccessScope(
    mode=CorpusAccessMode.GUEST,
    revision=DEFAULT_GUEST_ACCESS_CONFIGURATION.revision,
    allowed_seasons=frozenset(
        CorpusSeasonAccess(
            series_id=DEFAULT_GUEST_ACCESS_CONFIGURATION.series_id,
            season_number=season_number,
        )
        for season_number in DEFAULT_GUEST_ACCESS_CONFIGURATION.season_numbers
    ),
)
