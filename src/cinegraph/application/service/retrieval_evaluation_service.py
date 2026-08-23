from math import log2
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
        thresholds: RetrievalEvaluationThresholds = (DEFAULT_RETRIEVAL_EVALUATION_THRESHOLDS),
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
        recall_sum = 0.0
        ndcg_sum = 0.0
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
                    profile_watch_state=case.profile_watch_state or watch_state,
                    corpus_access_scope=case.corpus_access_scope,
                    limit=case.limit,
                )
            )
            retrieved_ids = tuple(
                dict.fromkeys(match.episode.episode_id for match in search.matches)
            )
            first_rank = next(
                (
                    rank
                    for rank, episode_id in enumerate(retrieved_ids, start=1)
                    if episode_id in case.expected_episode_ids
                ),
                None,
            )
            leaked_ids = frozenset(retrieved_ids) & case.forbidden_episode_ids
            relevant_count = len(case.expected_episode_ids)
            recalled_count = len(set(retrieved_ids) & case.expected_episode_ids)
            recall_at_k = recalled_count / relevant_count if relevant_count else 0.0
            dcg = sum(
                1.0 / log2(rank + 1)
                for rank, episode_id in enumerate(retrieved_ids, start=1)
                if episode_id in case.expected_episode_ids
            )
            ideal_count = min(case.limit, relevant_count)
            ideal_dcg = sum(1.0 / log2(rank + 1) for rank in range(1, ideal_count + 1))
            ndcg_at_k = dcg / ideal_dcg if ideal_dcg else 0.0
            recall_sum += recall_at_k
            ndcg_sum += ndcg_at_k
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
                    recall_at_k=recall_at_k,
                    ndcg_at_k=ndcg_at_k,
                )
            )

        case_count = len(dataset.cases)
        hit_rate = hit_count / case_count if case_count else 0.0
        mean_reciprocal_rank = reciprocal_rank_sum / case_count if case_count else 0.0
        mean_recall_at_k = recall_sum / case_count if case_count else 0.0
        mean_ndcg_at_k = ndcg_sum / case_count if case_count else 0.0
        passed = (
            hit_rate >= self._thresholds.minimum_hit_rate
            and mean_reciprocal_rank >= self._thresholds.minimum_mean_reciprocal_rank
            and leak_count <= self._thresholds.maximum_forbidden_episode_leaks
            and mean_recall_at_k >= self._thresholds.minimum_recall_at_k
            and mean_ndcg_at_k >= self._thresholds.minimum_ndcg_at_k
        )
        return RetrievalEvaluationReport(
            case_results=tuple(case_results),
            hit_rate=hit_rate,
            mean_reciprocal_rank=mean_reciprocal_rank,
            forbidden_episode_leak_count=leak_count,
            passed=passed,
            mean_recall_at_k=mean_recall_at_k,
            mean_ndcg_at_k=mean_ndcg_at_k,
        )
