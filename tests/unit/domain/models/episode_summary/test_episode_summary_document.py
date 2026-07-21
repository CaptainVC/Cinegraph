from datetime import UTC, datetime
from uuid import UUID

import pytest

from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.domain.enums.enum import Language, RightsStatus
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.episode_summary import EpisodeSummaryDocument
from tests.factories import make_episode_ref


SOURCE_VERSION_ID = UUID("00000000-0000-0000-0000-000000000501")
OTHER_SOURCE_VERSION_ID = UUID("00000000-0000-0000-0000-000000000502")
REVISION_TIMESTAMP = datetime(2026, 7, 21, 10, 0, tzinfo=UTC)
CANONICAL_URL = "https://en.wikipedia.org/wiki/Pilot_(Modern_Family)"
ATTRIBUTION = "Wikipedia contributors, CC BY-SA"


def make_summary(**overrides: object) -> EpisodeSummaryDocument:
    episode = make_episode_ref()
    values: dict[str, object] = {
        "summary_id": IdentifierGenerator.episode_summary_id(
            SOURCE_VERSION_ID,
            episode.episode_id,
            Language.ENGLISH,
        ),
        "source_version_id": SOURCE_VERSION_ID,
        "episode": episode,
        "text": "A concise episode-level summary.",
        "language": Language.ENGLISH,
        "rights_status": RightsStatus.RESTRICTED,
        "canonical_url": CANONICAL_URL,
        "revision_id": 456,
        "revision_timestamp": REVISION_TIMESTAMP,
        "attribution": ATTRIBUTION,
    }
    values.update(overrides)
    return EpisodeSummaryDocument(**values)


def test_creates_episode_summary_document() -> None:
    summary = make_summary()

    assert summary.language is Language.ENGLISH
    assert summary.rights_status is RightsStatus.RESTRICTED
    assert summary.canonical_url == CANONICAL_URL
    assert summary.revision_id == 456
    assert summary.revision_timestamp == REVISION_TIMESTAMP
    assert summary.attribution == ATTRIBUTION


@pytest.mark.parametrize(
    "overrides",
    [
        {"text": " Episode-level summary."},
        {"text": ""},
        {"language": "en"},
        {"canonical_url": f" {CANONICAL_URL}"},
        {"revision_id": 0},
        {"revision_timestamp": datetime(2026, 7, 21, 10, 0)},
        {"attribution": " Wikipedia contributors, CC BY-SA"},
    ],
)
def test_rejects_invalid_episode_summary_invariants(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(InvalidModelError):
        make_summary(**overrides)


def test_episode_summary_id_is_stable_for_same_source_episode_and_language() -> None:
    episode = make_episode_ref()

    assert IdentifierGenerator.episode_summary_id(
        SOURCE_VERSION_ID,
        episode.episode_id,
        Language.ENGLISH,
    ) == IdentifierGenerator.episode_summary_id(
        SOURCE_VERSION_ID,
        episode.episode_id,
        Language.ENGLISH,
    )


def test_episode_summary_id_changes_for_new_source_version() -> None:
    episode = make_episode_ref()

    assert IdentifierGenerator.episode_summary_id(
        SOURCE_VERSION_ID,
        episode.episode_id,
        Language.ENGLISH,
    ) != IdentifierGenerator.episode_summary_id(
        OTHER_SOURCE_VERSION_ID,
        episode.episode_id,
        Language.ENGLISH,
    )
