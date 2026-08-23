from dataclasses import dataclass
from math import isfinite

from cinegraph.common.error_messages import RetrievalErrorMessages


@dataclass(frozen=True, slots=True)
class HybridRetrievalConfiguration:
    candidate_overfetch_multiplier: int = 3
    candidate_overfetch_cap: int = 100
    maximum_member_overlap_ratio: float = 0.8
    max_requested_result_limit: int = 100

    def __post_init__(self) -> None:
        if (
            any(
                isinstance(value, bool) or not isinstance(value, int) or value < 1
                for value in (
                    self.candidate_overfetch_multiplier,
                    self.candidate_overfetch_cap,
                    self.max_requested_result_limit,
                )
            )
            or self.candidate_overfetch_cap < self.max_requested_result_limit
        ):
            raise ValueError(RetrievalErrorMessages.HYBRID_RETRIEVAL_CANDIDATE_LIMITS_INVALID)
        if (
            not isinstance(self.maximum_member_overlap_ratio, float)
            or not isfinite(self.maximum_member_overlap_ratio)
            or not 0.0 <= self.maximum_member_overlap_ratio <= 1.0
        ):
            raise ValueError(RetrievalErrorMessages.HYBRID_RETRIEVAL_OVERLAP_RATIO_INVALID)


DEFAULT_HYBRID_RETRIEVAL_CONFIGURATION = HybridRetrievalConfiguration()
