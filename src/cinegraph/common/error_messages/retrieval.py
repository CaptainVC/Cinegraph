class RetrievalErrorMessages:
    SEARCH_LIMIT_MUST_BE_POSITIVE = "Search limit must be at least one."
    EPISODE_VISIBILITY_SCOPE_SAFE_UNTIL_MS_MUST_BE_NON_NEGATIVE = (
        "Episode visibility scope safe_until_ms must be non-negative."
    )
    RETRIEVAL_SCOPE_EPISODE_SCOPES_MUST_BE_IMMUTABLE = (
        "Retrieval scope episode scopes must be immutable."
    )
    RETRIEVAL_SCOPE_EPISODES_MUST_MATCH_SERIES = (
        "Retrieval scope episodes must match the series."
    )
    RETRIEVAL_SCOPE_CANNOT_HAVE_DUPLICATE_EPISODES = (
        "Retrieval scope cannot contain duplicate episode IDs."
    )
    CANDIDATE_EPISODES_MUST_MATCH_SERIES = (
        "Candidate episodes must match the requested series."
    )
