class ConversationErrorMessages:
    SERIES_QUERY_IDENTITIES_MUST_BE_UUID = "Series conversation identities must be UUIDs."
    SERIES_QUERY_SCOPE_REVISION_MUST_BE_TRIMMED = "Series conversation scope revision must be trimmed and nonempty."
    SERIES_QUERY_QUESTION_MUST_BE_BOUNDED = "Series conversation question must be trimmed and bounded."
    SERIES_QUERY_CANDIDATE_LIMIT_EXCEEDED = "Series conversation candidate limit exceeded."
    SERIES_QUERY_CANDIDATES_MUST_BE_VALID = "Series conversation candidates must be non-empty and valid."
    SERIES_QUERY_CANDIDATES_MUST_SHARE_SERIES = "Series conversation candidates must belong to one series."
    BINDING_WATCH_STATE_VERSION_MUST_BE_NON_NEGATIVE = (
        "Conversation binding watch-state version must be non-negative."
    )
    BINDING_PERMISSION_SCOPE_REVISION_MUST_BE_NONEMPTY = (
        "Conversation binding permission-scope revision must be nonempty."
    )
    BINDING_PERMISSION_SCOPE_REVISION_MUST_MATCH_ACCESS_SCOPE = (
        "Conversation binding permission revision must match its corpus-access scope."
    )
    THREAD_PROFILE_MISMATCH = (
        "Conversation thread {thread_id} is bound to a different profile."
    )
    THREAD_WATCH_STATE_MISMATCH = (
        "Conversation thread {thread_id} is bound to a different watch-state version."
    )
    THREAD_SCOPE_MISMATCH = (
        "Conversation thread {thread_id} is bound to a different permission scope."
    )
