from uuid import UUID

from cinegraph.application.models.retrieval_evaluation import (
    RetrievalEvaluationCaseResult,
    RetrievalEvaluationDataset,
    RetrievalEvaluationReport,
)
from cinegraph.application.models.search_visible_hybrid_segments import (
    SearchVisibleHybridSegmentsQuery,
)
from cinegraph.application.service.search_visible_hybrid_segments_service import (
    SearchVisibleHybridSegmentsService,
)
from cinegraph.config import (
    DEFAULT_RETRIEVAL_EVALUATION_THRESHOLDS,
    RetrievalEvaluationThresholds,
)
from cinegraph.domain.enums.enum import SpoilerMode
from cinegraph.domain.models.watch_state import ProfileWatchState


EVALUATION_PROFILE_ID = UUID("00000000-0000-0000-0000-0000000000e1")


class RetrievalEvaluationService:
    def __init__(
        self,
        search_service: SearchVisibleHybridSegmentsService,
        thresholds: RetrievalEvaluationThresholds = (
            DEFAULT_RETRIEVAL_EVALUATION_THRESHOLDS
        ),
    ) -> None:
        self._search_service = search_service
        self._thresholds = thresholds

    # Measure ranking quality and fail-closed access leakage across all cases.
    def execute(
        self,
        dataset: RetrievalEvaluationDataset,
    ) -> RetrievalEvaluationReport:
        case_results = []
        reciprocal_rank_sum = 0.0
        hit_count = 0
        leak_count = 0
        watch_state = ProfileWatchState(
            profile_id=EVALUATION_PROFILE_ID,
            profile_name="Retrieval evaluation",
            spoiler_mode=SpoilerMode.RELAXED,
        )
        for case in dataset.cases:
            search = self._search_service.execute(
                SearchVisibleHybridSegmentsQuery(
                    query=case.query,
                    series_id=case.series_id,
                    candidate_episodes=case.candidate_episodes,
                    profile_watch_state=watch_state,
                    corpus_access_scope=case.corpus_access_scope,
                    limit=case.limit,
                )
            )
            retrieved_ids = tuple(match.episode.episode_id for match in search.matches)
            first_rank = next(
                (
                    rank
                    for rank, episode_id in enumerate(retrieved_ids, start=1)
                    if episode_id in case.expected_episode_ids
                ),
                None,
            )
            leaked_ids = frozenset(retrieved_ids) & case.forbidden_episode_ids
            if first_rank is not None:
                hit_count += 1
                reciprocal_rank_sum += 1.0 / first_rank
            leak_count += len(leaked_ids)
            case_results.append(
                RetrievalEvaluationCaseResult(
                    case_id=case.case_id,
                    retrieved_episode_ids=retrieved_ids,
                    first_expected_rank=first_rank,
                    leaked_episode_ids=leaked_ids,
                )
            )

        case_count = len(dataset.cases)
        hit_rate = hit_count / case_count if case_count else 0.0
        mean_reciprocal_rank = (
            reciprocal_rank_sum / case_count if case_count else 0.0
        )
        passed = (
            hit_rate >= self._thresholds.minimum_hit_rate
            and mean_reciprocal_rank
            >= self._thresholds.minimum_mean_reciprocal_rank
            and leak_count
            <= self._thresholds.maximum_forbidden_episode_leaks
        )
        return RetrievalEvaluationReport(
            case_results=tuple(case_results),
            hit_rate=hit_rate,
            mean_reciprocal_rank=mean_reciprocal_rank,
            forbidden_episode_leak_count=leak_count,
            passed=passed,
        )
