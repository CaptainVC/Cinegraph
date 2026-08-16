from dataclasses import dataclass
from uuid import UUID

from cinegraph.domain.models.access import CorpusAccessScope
from cinegraph.domain.models.watch_state import EpisodeRef


@dataclass(frozen=True, slots=True)
class RetrievalEvaluationCase:
    case_id: str
    query: str
    series_id: UUID
    candidate_episodes: tuple[EpisodeRef, ...]
    expected_episode_ids: frozenset[UUID]
    forbidden_episode_ids: frozenset[UUID]
    corpus_access_scope: CorpusAccessScope
    limit: int


@dataclass(frozen=True, slots=True)
class RetrievalEvaluationDataset:
    schema_version: int
    cases: tuple[RetrievalEvaluationCase, ...]


@dataclass(frozen=True, slots=True)
class RetrievalEvaluationCaseResult:
    case_id: str
    retrieved_episode_ids: tuple[UUID, ...]
    first_expected_rank: int | None
    leaked_episode_ids: frozenset[UUID]

    @property
    def hit(self) -> bool:
        return self.first_expected_rank is not None


@dataclass(frozen=True, slots=True)
class RetrievalEvaluationReport:
    case_results: tuple[RetrievalEvaluationCaseResult, ...]
    hit_rate: float
    mean_reciprocal_rank: float
    forbidden_episode_leak_count: int
    passed: bool
