class CatalogueErrorMessages:
    EPISODE_NUMBER_MUST_BE_POSITIVE = "Episode number must be a positive integer."
    EPISODE_RUNTIME_MUST_BE_POSITIVE = "Episode runtime must be a positive integer."
    SEASON_NUMBER_MUST_BE_POSITIVE = "Season number must be a positive integer."
    SEASON_MUST_CONTAIN_EPISODES = "Season must contain at least one episode."
    SEASON_EPISODES_MUST_BE_IMMUTABLE = "Season episodes must be immutable."
    SEASON_EPISODES_MUST_MATCH_CONTAINER = (
        "Season episodes must belong to their containing season."
    )
    SEASON_CANNOT_HAVE_DUPLICATE_EPISODE_IDS = (
        "Season cannot contain duplicate episode IDs."
    )
    SEASON_CANNOT_HAVE_DUPLICATE_EPISODE_NUMBERS = (
        "Season cannot contain duplicate episode numbers."
    )
    SERIES_NAME_MUST_BE_TRIMMED = (
        "Series name cannot be empty or have leading/trailing whitespace."
    )
    SERIES_MUST_CONTAIN_SEASONS = "Series must contain at least one season."
    SERIES_SEASONS_MUST_BE_IMMUTABLE = "Series seasons must be immutable."
    SERIES_SEASONS_MUST_MATCH_CONTAINER = (
        "Series seasons must belong to their containing series."
    )
    SERIES_CANNOT_HAVE_DUPLICATE_SEASON_IDS = (
        "Series cannot contain duplicate season IDs."
    )
    SERIES_CANNOT_HAVE_DUPLICATE_SEASON_NUMBERS = (
        "Series cannot contain duplicate season numbers."
    )
