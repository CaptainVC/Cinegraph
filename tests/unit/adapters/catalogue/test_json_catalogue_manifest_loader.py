import json
from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest

from cinegraph.adapters.catalogue import JsonCatalogueManifestLoader
from cinegraph.common.error_messages import CatalogueErrorMessages
from cinegraph.domain.exceptions.errors import InvalidModelError

SERIES_ID = "00000000-0000-0000-0000-000000000011"
SEASON_ONE_ID = "00000000-0000-0000-0000-000000000101"
SEASON_TWO_ID = "00000000-0000-0000-0000-000000000102"
EPISODE_ONE_ID = "00000000-0000-0000-0000-000000001001"
EPISODE_TWO_ID = "00000000-0000-0000-0000-000000001002"


def make_manifest_data() -> dict:
    return {
        "schema_version": 1,
        "series": [
            {
                "series_id": SERIES_ID,
                "series_name": "Example Family",
                "seasons": [
                    {
                        "season_id": SEASON_TWO_ID,
                        "season_number": 2,
                        "episodes": [
                            {
                                "episode_id": EPISODE_TWO_ID,
                                "episode_number": 2,
                                "episode_title": "Second Episode",
                                "runtime_seconds": 1_320,
                            }
                        ],
                    },
                    {
                        "season_id": SEASON_ONE_ID,
                        "season_number": 1,
                        "episodes": [
                            {
                                "episode_id": EPISODE_ONE_ID,
                                "episode_number": 1,
                                "episode_title": "First Episode",
                                "synopsis": "A family meets for dinner.",
                            }
                        ],
                    },
                ],
            }
        ],
    }


def write_manifest(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_loader_sorts_catalogue_and_builds_canonical_episode_refs(tmp_path: Path) -> None:
    path = tmp_path / "catalogue.json"
    write_manifest(path, make_manifest_data())

    loaded = JsonCatalogueManifestLoader().load(path)

    assert len(loaded.content_sha256) == 64
    series = loaded.manifest.series[0]
    assert series.series_id == UUID(SERIES_ID)
    assert [season.season_number for season in series.seasons] == [1, 2]
    assert series.seasons[0].episodes[0].episode_title == "First Episode"
    assert [reference.episode_id for reference in loaded.manifest.episode_refs()] == [
        UUID(EPISODE_ONE_ID),
        UUID(EPISODE_TWO_ID),
    ]


def test_equivalent_input_order_and_format_produce_same_digest(tmp_path: Path) -> None:
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    data = make_manifest_data()
    write_manifest(first_path, data)
    reordered = deepcopy(data)
    reordered["series"][0]["seasons"].reverse()
    second_path.write_text(json.dumps(reordered, separators=(",", ":")), encoding="utf-8")

    first = JsonCatalogueManifestLoader().load(first_path)
    second = JsonCatalogueManifestLoader().load(second_path)

    assert first == second


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.update({"unknown": True}),
        lambda data: data.update({"schema_version": 2}),
        lambda data: data["series"][0].update({"series_name": " Example"}),
        lambda data: data["series"][0]["seasons"].append(
            {
                "season_id": SEASON_TWO_ID,
                "season_number": 3,
                "episodes": [
                    {
                        "episode_id": "00000000-0000-0000-0000-000000001003",
                        "episode_number": 1,
                        "episode_title": "Duplicate season ID",
                    }
                ],
            }
        ),
    ],
)
def test_invalid_structure_or_domain_identity_is_wrapped_in_central_error(
    tmp_path: Path,
    mutation,
) -> None:
    data = make_manifest_data()
    mutation(data)
    path = tmp_path / "invalid.json"
    write_manifest(path, data)

    with pytest.raises(
        InvalidModelError,
        match=CatalogueErrorMessages.CATALOGUE_MANIFEST_STRUCTURE_MUST_BE_VALID,
    ):
        JsonCatalogueManifestLoader().load(path)


def test_invalid_json_uses_distinct_central_error(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text("{invalid", encoding="utf-8")

    with pytest.raises(
        InvalidModelError,
        match=CatalogueErrorMessages.CATALOGUE_MANIFEST_JSON_MUST_BE_VALID,
    ):
        JsonCatalogueManifestLoader().load(path)


def test_missing_file_uses_distinct_central_error(tmp_path: Path) -> None:
    with pytest.raises(
        InvalidModelError,
        match=CatalogueErrorMessages.CATALOGUE_MANIFEST_PATH_MUST_BE_FILE,
    ):
        JsonCatalogueManifestLoader().load(tmp_path / "missing.json")
