class RecommendationErrorMessages:
    SERIES_MUST_EXIST = "Recommendation series must exist in the catalogue."
    MOOD_MUST_BE_TRIMMED = "Recommendation mood must be non-empty and trimmed."
    WATCH_PREFERENCE_MUST_BE_VALID = "Recommendation watch preference must be valid."
    TERMS_MUST_BE_IMMUTABLE = "Recommendation terms must be immutable tuples."
    TERMS_MUST_BE_UNIQUE = "Recommendation terms must be unique and trimmed."
    REQUESTED_COUNT_MUST_BE_VALID = (
        "Recommendation count must be within the configured limit."
    )
    MAXIMUM_RUNTIME_MUST_BE_POSITIVE = (
        "Recommendation maximum runtime must be a positive integer."
    )
    RANKER_RESULT_MUST_REFERENCE_CANDIDATE = (
        "Recommendation ranker returned an episode outside the candidate set."
    )
    RANKER_RESULTS_MUST_BE_UNIQUE = (
        "Recommendation ranker returned a duplicate episode."
    )
    RANKER_SCORE_MUST_BE_PROBABILITY = (
        "Recommendation ranker score must be finite and between zero and one."
    )
    RANKER_REASON_MUST_BE_TRIMMED = (
        "Recommendation reason must be non-empty and trimmed."
    )
    RANKER_CITATIONS_MUST_BE_VISIBLE = (
        "Recommendation citations must be non-empty and belong to candidate evidence."
    )
    WORKFLOW_RESULT_MUST_EXIST = (
        "Recommendation workflow completed without a result."
    )
