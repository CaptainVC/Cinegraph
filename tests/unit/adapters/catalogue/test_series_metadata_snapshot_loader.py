import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest

from cinegraph.adapters.catalogue.json_catalogue_manifest_loader import JsonCatalogueManifestLoader
from cinegraph.adapters.catalogue.series_metadata_snapshot_loader import (
    JsonSeriesMetadataSnapshotLoader,
    parse_series_metadata_snapshot,
)
from cinegraph.application.serialization.series_metadata_snapshot_serializer import (
    canonical_metadata_json,
    export_json,
)
from cinegraph.common.identifiers.generator import IdentifierGenerator
from cinegraph.domain.enums.enum import (
    RightsStatus,
    SourceAcquisitionMethod,
    SourceKind,
    SourceReviewStatus,
    SourceVersionStatus,
)
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.catalogue.catalogue_manifest import CatalogueManifest
from cinegraph.domain.models.catalogue.episode import Episode
from cinegraph.domain.models.catalogue.season import Season
from cinegraph.domain.models.catalogue.series import Series
from cinegraph.domain.models.series_metadata import (
    ArtworkAsset,
    CreditedPerson,
    CreditKind,
    EpisodeCastMetadata,
    SeriesMetadataSnapshot,
)
from cinegraph.domain.models.source.source_document import SourceDocument
from cinegraph.domain.models.source.source_version import SourceVersion
from cinegraph.domain.models.watch_state import EpisodePosition, EpisodeRef
from cinegraph.ingestion.series_metadata.ingest_series_metadata import IngestSeriesMetadataResult

SERIES_ID = UUID("00000000-0000-0000-0000-000000000011")
SEASON_ID = UUID("00000000-0000-0000-0000-000000000101")
EPISODE_IDS = (
    UUID("00000000-0000-0000-0000-000000001001"),
    UUID("00000000-0000-0000-0000-000000001002"),
)
ACQUIRED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def catalogue() -> CatalogueManifest:
    return CatalogueManifest(
        1,
        (
            Series(
                SERIES_ID,
                "Modern Family",
                (
                    Season(
                        SERIES_ID,
                        SEASON_ID,
                        1,
                        (
                            Episode(SERIES_ID, SEASON_ID, EPISODE_IDS[0], 1, "Pilot"),
                            Episode(SERIES_ID, SEASON_ID, EPISODE_IDS[1], 2, "The Bicycle Thief"),
                        ),
                    ),
                ),
            ),
        ),
    )


def manifest_file(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "series": [
                    {
                        "series_id": str(SERIES_ID),
                        "series_name": "Modern Family",
                        "seasons": [
                            {
                                "season_id": str(SEASON_ID),
                                "season_number": 1,
                                "episodes": [
                                    {
                                        "episode_id": str(EPISODE_IDS[0]),
                                        "episode_number": 1,
                                        "episode_title": "Pilot",
                                    },
                                    {
                                        "episode_id": str(EPISODE_IDS[1]),
                                        "episode_number": 2,
                                        "episode_title": "The Bicycle Thief",
                                    },
                                ],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def snapshot_and_version(
    *,
    review_status: SourceReviewStatus = SourceReviewStatus.PENDING,
    with_poster: bool = False,
    two_characters_same_person: bool = False,
    provider_title_case_variant: bool = False,
):
    episodes = tuple(
        EpisodeCastMetadata(
            EpisodeRef(SERIES_ID, SEASON_ID, episode_id, EpisodePosition(1, number)),
            100 + number,
            title.upper() if provider_title_case_variant and number == 1 else title,
            f"https://www.tvmaze.com/episodes/{100 + number}/episode-{number}",
            (),
        )
        for episode_id, number, title in (
            (EPISODE_IDS[0], 1, "Pilot"),
            (EPISODE_IDS[1], 2, "The Bicycle Thief"),
        )
    )
    from cinegraph.ports.dto.fetched_series_metadata import FetchedSeriesMetadata

    regular_cast = (
        CreditedPerson(
            501,
            "Alex Actor",
            "https://www.tvmaze.com/people/501/alex-actor",
            "Character One",
            601,
            "https://www.tvmaze.com/characters/601/character-one",
            CreditKind.REGULAR,
        ),
        CreditedPerson(
            501,
            "Alex Actor",
            "https://www.tvmaze.com/people/501/alex-actor",
            "Character Two",
            602,
            "https://www.tvmaze.com/characters/602/character-two",
            CreditKind.REGULAR,
        ),
    ) if two_characters_same_person else ()
    fetched = FetchedSeriesMetadata(
        "TVmaze",
        80,
        "Modern Family",
        "https://www.tvmaze.com/shows/80/modern-family",
        ArtworkAsset(
            "https://static.tvmaze.com/uploads/images/original_untouched/1/2.jpg",
            "https://www.tvmaze.com/shows/80/modern-family",
            "https://static.tvmaze.com/uploads/images/medium_portrait/1/2.jpg",
            "https://static.tvmaze.com/uploads/images/original_untouched/1/2.jpg",
            "/uploads/images/1/2.jpg",
            680,
            1_000,
            "TVmaze, licensed under CC BY-SA",
            "Creative Commons Attribution-ShareAlike 4.0 International",
            "https://creativecommons.org/licenses/by-sa/4.0/",
            ACQUIRED_AT,
        )
        if with_poster
        else None,
        regular_cast,
        episodes,
        ACQUIRED_AT,
        "TVmaze, licensed under CC BY-SA",
        "Creative Commons Attribution-ShareAlike 4.0 International",
        "https://creativecommons.org/licenses/by-sa/4.0/",
    )
    content_hash = sha256(canonical_metadata_json(fetched).encode()).hexdigest()
    source_document_id = IdentifierGenerator.series_metadata_source_document_id(SERIES_ID, "tvmaze")
    source_version_id = IdentifierGenerator.source_version_id(source_document_id, content_hash)
    version = SourceVersion(
        source_version_id,
        source_document_id,
        content_hash,
        RightsStatus.ALLOWED,
        SourceAcquisitionMethod.TVMAZE_API,
        review_status,
        SourceVersionStatus.ACTIVE,
        ACQUIRED_AT,
        reviewed_by="test" if review_status is not SourceReviewStatus.PENDING else None,
        reviewed_at=ACQUIRED_AT if review_status is not SourceReviewStatus.PENDING else None,
    )
    snapshot = SeriesMetadataSnapshot(
        SERIES_ID,
        source_version_id,
        fetched.provider_name,
        fetched.provider_show_id,
        fetched.title,
        fetched.canonical_url,
        fetched.poster,
        fetched.regular_cast,
        fetched.episodes,
        RightsStatus.ALLOWED,
        fetched.attribution,
        fetched.license_name,
        fetched.license_url,
    )
    document = SourceDocument(source_document_id, "TVmaze metadata for Modern Family", SourceKind.METADATA, "tvmaze")
    return document, version, snapshot


def write_snapshot(
    path: Path,
    status: SourceReviewStatus = SourceReviewStatus.PENDING,
    *,
    with_poster: bool = False,
    two_characters_same_person: bool = False,
    provider_title_case_variant: bool = False,
) -> dict:
    document, version, snapshot = snapshot_and_version(
        review_status=status,
        with_poster=with_poster,
        two_characters_same_person=two_characters_same_person,
        provider_title_case_variant=provider_title_case_variant,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        export_json(document, IngestSeriesMetadataResult(version, snapshot, False)),
        encoding="utf-8",
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_same_person_with_two_distinct_characters_is_valid(tmp_path: Path) -> None:
    manifest = tmp_path / "catalogue.json"
    manifest_file(manifest)
    path = tmp_path / "approved" / "metadata.json"
    write_snapshot(path, SourceReviewStatus.REVIEWED, two_characters_same_person=True)
    loaded = JsonSeriesMetadataSnapshotLoader().load_directory(
        path.parent, JsonCatalogueManifestLoader().load(manifest).manifest
    )
    assert len(loaded[SERIES_ID].regular_cast) == 2


def test_episode_title_case_difference_is_not_treated_as_a_different_episode(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "catalogue.json"
    manifest_file(manifest)
    path = tmp_path / "approved" / "metadata.json"
    write_snapshot(
        path,
        SourceReviewStatus.REVIEWED,
        provider_title_case_variant=True,
    )
    loaded = JsonSeriesMetadataSnapshotLoader().load_directory(
        path.parent, JsonCatalogueManifestLoader().load(manifest).manifest
    )
    assert loaded[SERIES_ID].episodes[0].title == "PILOT"


def test_valid_export_round_trip_and_missing_directory(tmp_path: Path) -> None:
    document = tmp_path / "catalogue.json"
    manifest_file(document)
    loaded_catalogue = JsonCatalogueManifestLoader().load(document).manifest
    approved = tmp_path / "approved"
    assert JsonSeriesMetadataSnapshotLoader().load_directory(approved, loaded_catalogue) == {}
    file = approved / "modern-family.json"
    payload = write_snapshot(file, SourceReviewStatus.REVIEWED)
    loaded = JsonSeriesMetadataSnapshotLoader().load_directory(approved, loaded_catalogue)
    assert loaded[SERIES_ID].title == "Modern Family"
    assert payload["source_version"]["reviewed_by"] == "test"


@pytest.mark.parametrize("status", [SourceReviewStatus.PENDING, SourceReviewStatus.REJECTED])
def test_pending_and_rejected_outputs_are_not_runtime_visible(tmp_path: Path, status: SourceReviewStatus) -> None:
    manifest = tmp_path / "catalogue.json"
    manifest_file(manifest)
    path = tmp_path / "approved" / "metadata.json"
    write_snapshot(path, status)
    with pytest.raises(InvalidModelError):
        JsonSeriesMetadataSnapshotLoader().load_directory(
            path.parent, JsonCatalogueManifestLoader().load(manifest).manifest
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload["source_version"].update({"content_hash": "b" * 64}),
        lambda payload: payload["metadata"].update({"canonical_url": "https://evil.example/show"}),
        lambda payload: payload["metadata"]["episodes"][0].update({"title": "Wrong"}),
        lambda payload: payload["metadata"]["episodes"].append(payload["metadata"]["episodes"][0]),
        lambda payload: payload.update({"unknown": True}),
    ],
)
def test_tampered_host_catalogue_duplicate_and_extra_fields_fail_closed(
    tmp_path: Path, mutation
) -> None:
    manifest = tmp_path / "catalogue.json"
    manifest_file(manifest)
    path = tmp_path / "approved" / "metadata.json"
    payload = write_snapshot(path, SourceReviewStatus.REVIEWED)
    mutation(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(InvalidModelError):
        parse_series_metadata_snapshot(path, JsonCatalogueManifestLoader().load(manifest).manifest)
