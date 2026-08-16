import json
from pathlib import Path

import pytest

from cinegraph.adapters.catalogue import JsonCatalogueManifestLoader
from cinegraph.adapters.evaluation import JsonRetrievalEvaluationDatasetLoader
from cinegraph.common.error_messages import EvaluationErrorMessages
from cinegraph.domain.enums.enum import CorpusAccessMode
from cinegraph.domain.exceptions.errors import InvalidModelError


def write_catalogue(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "series": [
                    {
                        "series_id": "00000000-0000-0000-0000-000000000011",
                        "series_name": "Example Family",
                        "seasons": [
                            {
                                "season_id": "00000000-0000-0000-0000-000000000101",
                                "season_number": 1,
                                "episodes": [
                                    {
                                        "episode_id": "00000000-0000-0000-0000-000000001001",
                                        "episode_number": 1,
                                        "episode_title": "Pilot",
                                    },
                                    {
                                        "episode_id": "00000000-0000-0000-0000-000000001002",
                                        "episode_number": 2,
                                        "episode_title": "Second",
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


def make_dataset() -> dict:
    return {
        "schema_version": 1,
        "series_id": "00000000-0000-0000-0000-000000000011",
        "cases": [
            {
                "case_id": "pilot-query",
                "query": "family dinner",
                "access_mode": "guest",
                "candidate_seasons": [1],
                "expected_episodes": [
                    {"season_number": 1, "episode_number": 1}
                ],
                "forbidden_episodes": [
                    {"season_number": 1, "episode_number": 2}
                ],
                "limit": 5,
            }
        ],
    }


def test_loader_resolves_positions_and_access_scope(tmp_path: Path) -> None:
    catalogue_path = tmp_path / "catalogue.json"
    dataset_path = tmp_path / "evaluation.json"
    write_catalogue(catalogue_path)
    dataset_path.write_text(json.dumps(make_dataset()), encoding="utf-8")
    manifest = JsonCatalogueManifestLoader().load(catalogue_path).manifest

    dataset = JsonRetrievalEvaluationDatasetLoader().load(manifest, dataset_path)

    case = dataset.cases[0]
    assert case.case_id == "pilot-query"
    assert case.corpus_access_scope.mode is CorpusAccessMode.GUEST
    assert len(case.candidate_episodes) == 2
    assert case.candidate_episodes[0].episode_id in case.expected_episode_ids
    assert case.candidate_episodes[1].episode_id in case.forbidden_episode_ids


@pytest.mark.parametrize("mutation", ["unknown-position", "overlap", "duplicate-id"])
def test_invalid_dataset_identity_fails_closed(tmp_path: Path, mutation: str) -> None:
    catalogue_path = tmp_path / "catalogue.json"
    dataset_path = tmp_path / "evaluation.json"
    write_catalogue(catalogue_path)
    data = make_dataset()
    expected_message = EvaluationErrorMessages.RETRIEVAL_EVALUATION_EPISODE_MUST_EXIST
    if mutation == "unknown-position":
        data["cases"][0]["expected_episodes"][0]["episode_number"] = 99
    elif mutation == "overlap":
        data["cases"][0]["forbidden_episodes"] = data["cases"][0]["expected_episodes"]
        expected_message = EvaluationErrorMessages.RETRIEVAL_EVALUATION_SETS_MUST_NOT_OVERLAP
    else:
        data["cases"].append(dict(data["cases"][0]))
        expected_message = EvaluationErrorMessages.RETRIEVAL_EVALUATION_CASE_IDS_MUST_BE_UNIQUE
    dataset_path.write_text(json.dumps(data), encoding="utf-8")
    manifest = JsonCatalogueManifestLoader().load(catalogue_path).manifest

    with pytest.raises(InvalidModelError, match=expected_message):
        JsonRetrievalEvaluationDatasetLoader().load(manifest, dataset_path)
