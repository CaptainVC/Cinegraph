class EvaluationErrorMessages:
    RETRIEVAL_EVALUATION_DATASET_MUST_BE_VALID = (
        "Retrieval evaluation dataset must match schema version 1."
    )
    RETRIEVAL_EVALUATION_CASE_IDS_MUST_BE_UNIQUE = (
        "Retrieval evaluation case IDs must be unique."
    )
    RETRIEVAL_EVALUATION_EPISODE_MUST_EXIST = (
        "Retrieval evaluation episode positions must exist in the catalogue."
    )
    RETRIEVAL_EVALUATION_EXPECTED_EPISODES_REQUIRED = (
        "Retrieval evaluation cases must contain expected episodes."
    )
    RETRIEVAL_EVALUATION_SETS_MUST_NOT_OVERLAP = (
        "Expected and forbidden evaluation episodes must not overlap."
    )
    RETRIEVAL_EVALUATION_THRESHOLD_MUST_BE_PROBABILITY = (
        "Retrieval evaluation rate thresholds must be between zero and one."
    )
    RETRIEVAL_EVALUATION_MAXIMUM_LEAKS_MUST_BE_NON_NEGATIVE = (
        "Retrieval evaluation maximum leaks must be a non-negative integer."
    )
