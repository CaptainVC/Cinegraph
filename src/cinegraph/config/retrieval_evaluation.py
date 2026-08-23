from dataclasses import dataclass

from cinegraph.common.error_messages import EvaluationErrorMessages


@dataclass(frozen=True, slots=True)
class RetrievalEvaluationThresholds:
    minimum_hit_rate: float
    minimum_mean_reciprocal_rank: float
    maximum_forbidden_episode_leaks: int
    minimum_recall_at_k: float = 0.80
    minimum_ndcg_at_k: float = 0.60

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0.0 <= value <= 1.0
            for value in (
                self.minimum_hit_rate,
                self.minimum_mean_reciprocal_rank,
                self.minimum_recall_at_k,
                self.minimum_ndcg_at_k,
            )
        ):
            raise ValueError(
                EvaluationErrorMessages.RETRIEVAL_EVALUATION_THRESHOLD_MUST_BE_PROBABILITY
            )
        if (
            isinstance(self.maximum_forbidden_episode_leaks, bool)
            or not isinstance(self.maximum_forbidden_episode_leaks, int)
            or self.maximum_forbidden_episode_leaks < 0
        ):
            raise ValueError(
                EvaluationErrorMessages.RETRIEVAL_EVALUATION_MAXIMUM_LEAKS_MUST_BE_NON_NEGATIVE
            )


DEFAULT_RETRIEVAL_EVALUATION_THRESHOLDS = RetrievalEvaluationThresholds(
    minimum_hit_rate=0.80,
    minimum_mean_reciprocal_rank=0.60,
    maximum_forbidden_episode_leaks=0,
    minimum_recall_at_k=0.80,
    minimum_ndcg_at_k=0.60,
)
