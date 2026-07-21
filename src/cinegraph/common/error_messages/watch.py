class WatchErrorMessages:
    EPISODE_POSITION_SEASON_MUST_BE_POSITIVE = (
        "Season number must be a positive integer."
    )
    EPISODE_POSITION_EPISODE_MUST_BE_POSITIVE = (
        "Episode number must be a positive integer."
    )
    SAFE_UNTIL_MS_MUST_BE_NON_NEGATIVE = (
        "safe_until_ms must be a non-negative integer or None."
    )
    COMPLETED_EPISODE_CANNOT_HAVE_SAFE_UNTIL_MS = (
        "safe_until_ms must be None if is_completed is True."
    )
    PARTIAL_EPISODE_REQUIRES_SAFE_UNTIL_MS = (
        "A partial episode watch requires a safe playback position."
    )
    EPISODE_PROGRESS_MUST_BE_IMMUTABLE = "Episode progress must be immutable."
    MANUALLY_ALLOWED_EPISODES_MUST_BE_IMMUTABLE = (
        "Manually allowed episodes must be immutable."
    )
    SERIES_WATCH_STATE_EPISODES_MUST_MATCH_SERIES = (
        "All EpisodeRefs must belong to the same series as the SeriesWatchState."
    )
    SERIES_WATCH_STATE_CANNOT_HAVE_DUPLICATE_PROGRESS = (
        "Only one progress record per episode is allowed."
    )
    EPISODE_MUST_MATCH_SERIES_WATCH_STATE = (
        "EpisodeRef must belong to the same series as the SeriesWatchState."
    )
    SEASON_OPERATION_REQUIRES_EPISODES = (
        "A season watch operation requires at least one episode."
    )
    SEASON_OPERATION_EPISODES_MUST_SHARE_SERIES = (
        "All episodes in a season watch operation must belong to one series."
    )
    SEASON_OPERATION_EPISODES_MUST_SHARE_SEASON = (
        "All episodes in a season watch operation must belong to one season."
    )
    PROFILE_ID_CANNOT_BE_EMPTY = "Profile ID cannot be empty."
    PROFILE_NAME_MUST_BE_TRIMMED = (
        "Profile name cannot be empty or have leading/trailing whitespace."
    )
    SERIES_WATCH_STATES_MUST_BE_IMMUTABLE = (
        "Series watch states must be immutable."
    )
    PROFILE_CANNOT_HAVE_DUPLICATE_SERIES_WATCH_STATES = (
        "Duplicate series IDs found in series_watch_states."
    )
    PROFILE_WATCH_STATE_VERSION_CANNOT_BE_NEGATIVE = (
        "Profile watch-state version cannot be negative."
    )
    WATCH_EVENT_TIMESTAMP_MUST_BE_TIMEZONE_AWARE = (
        "Watch event timestamp must be timezone-aware."
    )
    INITIAL_WATCH_STATES_MUST_HAVE_UNIQUE_PROFILE_IDS = (
        "Initial watch states must have unique profile IDs."
    )
    CONCURRENT_WATCH_PROGRESS = "Profile watch state was updated concurrently."
    EXPECTED_VERSION_MISMATCH = (
        "Updated watch state must advance its version by exactly one."
    )
    WATCH_EVENT_PROFILE_MISMATCH = (
        "Watch event profile does not match watch state profile."
    )
    WATCH_EVENT_NOT_FULLY_WATCHED = (
        "Watch event does not indicate that the episode was fully watched."
    )
    UNWATCHED_EVENT_REQUIRES_NO_PROGRESS = (
        "A marked-unwatched event requires no remaining episode progress."
    )
    NO_WATCH_STATE_FOUND_FOR_PROFILE_ID = (
        "Profile watch state not found for profile_id: {profile_id}"
    )
    NO_SEASON_FOUND_FOR_SERIES_ID = (
        "Season {season_id} was not found for series {series_id}."
    )
    SEASON_CATALOG_CANNOT_HAVE_DUPLICATE_EPISODE_IDS = (
        "A season catalog cannot contain duplicate episode IDs."
    )
