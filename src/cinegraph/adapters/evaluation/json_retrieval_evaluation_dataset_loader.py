from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from cinegraph.application.models.retrieval_evaluation import (
    RetrievalEvaluationCase,
    RetrievalEvaluationDataset,
)
from cinegraph.common.error_messages import EvaluationErrorMessages
from cinegraph.config import DEFAULT_GUEST_CORPUS_ACCESS_SCOPE
from cinegraph.domain.enums.enum import CorpusAccessMode
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.access import CorpusAccessScope
from cinegraph.domain.models.catalogue.catalogue_manifest import CatalogueManifest


class _DatasetModelBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _EpisodePositionModel(_DatasetModelBase):
    season_number: StrictInt = Field(ge=1)
    episode_number: StrictInt = Field(ge=1)


class _EvaluationCaseModel(_DatasetModelBase):
    case_id: StrictStr
    query: StrictStr
    access_mode: CorpusAccessMode
    candidate_seasons: tuple[StrictInt, ...] = Field(min_length=1)
    expected_episodes: tuple[_EpisodePositionModel, ...] = Field(min_length=1)
    forbidden_episodes: tuple[_EpisodePositionModel, ...] = ()
    limit: StrictInt = Field(default=10, ge=1)


class _EvaluationDatasetModel(_DatasetModelBase):
    schema_version: StrictInt
    series_id: UUID
    cases: tuple[_EvaluationCaseModel, ...] = Field(min_length=1)


class JsonRetrievalEvaluationDatasetLoader:
    # Resolve human-readable positions against the canonical catalogue graph.
    def load(
        self,
        manifest: CatalogueManifest,
        path: Path,
    ) -> RetrievalEvaluationDataset:
        try:
            source = _EvaluationDatasetModel.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as error:
            raise InvalidModelError(
                EvaluationErrorMessages.RETRIEVAL_EVALUATION_DATASET_MUST_BE_VALID
            ) from error
        if source.schema_version != 1:
            raise InvalidModelError(
                EvaluationErrorMessages.RETRIEVAL_EVALUATION_DATASET_MUST_BE_VALID
            )
        case_ids = tuple(item.case_id for item in source.cases)
        if (
            len(case_ids) != len(set(case_ids))
            or any(not value or value.strip() != value for value in case_ids)
        ):
            raise InvalidModelError(
                EvaluationErrorMessages.RETRIEVAL_EVALUATION_CASE_IDS_MUST_BE_UNIQUE
            )
        series = next(
            (item for item in manifest.series if item.series_id == source.series_id),
            None,
        )
        if series is None:
            raise InvalidModelError(
                EvaluationErrorMessages.RETRIEVAL_EVALUATION_EPISODE_MUST_EXIST
            )
        refs_by_position = {
            (
                reference.position.season_number,
                reference.position.episode_number,
            ): reference
            for reference in manifest.episode_refs()
            if reference.series_id == source.series_id
        }

        cases = []
        for item in source.cases:
            expected = self._resolve_positions(item.expected_episodes, refs_by_position)
            forbidden = self._resolve_positions(item.forbidden_episodes, refs_by_position)
            expected_ids = frozenset(value.episode_id for value in expected)
            forbidden_ids = frozenset(value.episode_id for value in forbidden)
            if expected_ids & forbidden_ids:
                raise InvalidModelError(
                    EvaluationErrorMessages.RETRIEVAL_EVALUATION_SETS_MUST_NOT_OVERLAP
                )
            candidate_seasons = set(item.candidate_seasons)
            candidate_episodes = tuple(
                value
                for position, value in refs_by_position.items()
                if position[0] in candidate_seasons
            )
            if not candidate_episodes or not expected_ids <= {
                value.episode_id for value in candidate_episodes
            }:
                raise InvalidModelError(
                    EvaluationErrorMessages.RETRIEVAL_EVALUATION_EPISODE_MUST_EXIST
                )
            access_scope = (
                DEFAULT_GUEST_CORPUS_ACCESS_SCOPE
                if item.access_mode is CorpusAccessMode.GUEST
                else CorpusAccessScope(
                    mode=CorpusAccessMode.AUTHENTICATED,
                    revision="retrieval-evaluation-authenticated-v1",
                    allowed_seasons=frozenset(),
                    unrestricted=True,
                )
            )
            cases.append(
                RetrievalEvaluationCase(
                    case_id=item.case_id,
                    query=item.query,
                    series_id=source.series_id,
                    candidate_episodes=candidate_episodes,
                    expected_episode_ids=expected_ids,
                    forbidden_episode_ids=forbidden_ids,
                    corpus_access_scope=access_scope,
                    limit=item.limit,
                )
            )
        return RetrievalEvaluationDataset(
            schema_version=source.schema_version,
            cases=tuple(cases),
        )

    @staticmethod
    def _resolve_positions(
        positions: tuple[_EpisodePositionModel, ...],
        refs_by_position: dict[tuple[int, int], object],
    ) -> tuple:
        try:
            return tuple(
                refs_by_position[(item.season_number, item.episode_number)]
                for item in positions
            )
        except KeyError as error:
            raise InvalidModelError(
                EvaluationErrorMessages.RETRIEVAL_EVALUATION_EPISODE_MUST_EXIST
            ) from error
