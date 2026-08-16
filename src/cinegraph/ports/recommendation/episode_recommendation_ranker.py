from typing import Protocol

from cinegraph.application.models.episode_recommendation import (
    RankedRecommendationDraft,
    RecommendationRankingRequest,
)


class EpisodeRecommendationRanker(Protocol):
    def rank(
        self,
        request: RecommendationRankingRequest,
    ) -> tuple[RankedRecommendationDraft, ...]: ...
