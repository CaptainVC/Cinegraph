from datetime import UTC, datetime
from uuid import UUID

import pytest

from cinegraph.domain.enums.enum import RightsStatus
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.series_metadata import (
    ArtworkAsset,
    CreditedPerson,
    CreditKind,
    EpisodeCastMetadata,
    SeriesMetadataSnapshot,
)
from cinegraph.domain.models.watch_state import EpisodePosition, EpisodeRef


def _credit(kind: CreditKind = CreditKind.GUEST) -> CreditedPerson:
    return CreditedPerson(
        provider_person_id=1,
        name="Performer",
        canonical_url="https://www.tvmaze.com/people/1/performer",
        character_name="Character",
        character_provider_id=2,
        character_canonical_url="https://www.tvmaze.com/characters/2/character",
        credit_kind=kind,
    )


def _episode(series_id: UUID, number: int = 1) -> EpisodeCastMetadata:
    return EpisodeCastMetadata(
        episode=EpisodeRef(
            series_id=series_id,
            season_id=UUID(int=10),
            episode_id=UUID(int=10 + number),
            position=EpisodePosition(1, number),
        ),
        provider_episode_id=100 + number,
        title=f"Episode {number}",
        canonical_url=f"https://www.tvmaze.com/episodes/{100 + number}/episode",
        guest_cast=(_credit(),),
    )


def test_artwork_requires_valid_urls_dimensions_and_timezone() -> None:
    with pytest.raises(InvalidModelError):
        ArtworkAsset(
            source_url="javascript:alert(1)",
            canonical_url="https://www.tvmaze.com/shows/80/modern-family",
            medium_url=None,
            original_url=None,
            provider_asset_id=None,
            width=None,
            height=None,
            attribution="TVmaze",
            license_name="CC BY-SA 4.0",
            license_url="https://creativecommons.org/licenses/by-sa/4.0/",
            retrieved_at=datetime.now(UTC),
        )

    with pytest.raises(InvalidModelError):
        ArtworkAsset(
            source_url="https://static.tvmaze.com/poster.jpg",
            canonical_url="https://www.tvmaze.com/shows/80/modern-family",
            medium_url=None,
            original_url=None,
            provider_asset_id=None,
            width=0,
            height=None,
            attribution="TVmaze",
            license_name="CC BY-SA 4.0",
            license_url="https://creativecommons.org/licenses/by-sa/4.0/",
            retrieved_at=datetime.now(UTC),
        )


def test_cast_rejects_boolean_provider_ids_and_wrong_credit_scope() -> None:
    with pytest.raises(InvalidModelError):
        CreditedPerson(
            provider_person_id=True,
            name="Performer",
            canonical_url="https://www.tvmaze.com/people/1/performer",
            character_name="Character",
            character_provider_id=2,
            character_canonical_url=None,
            credit_kind=CreditKind.REGULAR,
        )

    series_id = UUID(int=1)
    with pytest.raises(InvalidModelError):
        EpisodeCastMetadata(
            episode=_episode(series_id).episode,
            provider_episode_id=101,
            title="Episode 1",
            canonical_url="https://www.tvmaze.com/episodes/101/episode",
            guest_cast=(_credit(CreditKind.REGULAR),),
        )


def test_snapshot_requires_episode_order_series_coherence_and_explicit_license() -> (
    None
):
    series_id = UUID(int=1)
    with pytest.raises(InvalidModelError):
        SeriesMetadataSnapshot(
            series_id=series_id,
            source_version_id=UUID(int=2),
            provider_name="TVmaze",
            provider_show_id=80,
            title="Modern Family",
            canonical_url="https://www.tvmaze.com/shows/80/modern-family",
            poster=None,
            regular_cast=(),
            episodes=(_episode(series_id, 2), _episode(series_id, 1)),
            rights_status=RightsStatus.ALLOWED,
            attribution="TVmaze, licensed under CC BY-SA",
            license_name="Creative Commons Attribution-ShareAlike 4.0 International",
            license_url="https://creativecommons.org/licenses/by-sa/4.0/",
        )
