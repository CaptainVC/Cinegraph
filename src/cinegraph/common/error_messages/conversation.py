class ConversationErrorMessages:
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
