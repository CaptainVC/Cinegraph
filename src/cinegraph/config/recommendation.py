from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RecommendationConfiguration:
    maximum_requested_count: int
    maximum_ranker_candidates: int
    retrieval_evidence_limit: int
    maximum_characters: int
    maximum_excluded_themes: int
    maximum_term_length: int

    def __post_init__(self) -> None:
        values = (
            self.maximum_requested_count,
            self.maximum_ranker_candidates,
            self.retrieval_evidence_limit,
            self.maximum_characters,
            self.maximum_excluded_themes,
            self.maximum_term_length,
        )
        if any(value < 1 for value in values):
            raise ValueError("Recommendation configuration values must be positive.")
        if self.maximum_ranker_candidates < self.maximum_requested_count:
            raise ValueError(
                "Recommendation candidate limit must cover the requested limit."
            )


DEFAULT_RECOMMENDATION_CONFIGURATION = RecommendationConfiguration(
    maximum_requested_count=5,
    maximum_ranker_candidates=12,
    retrieval_evidence_limit=40,
    maximum_characters=8,
    maximum_excluded_themes=8,
    maximum_term_length=80,
)
