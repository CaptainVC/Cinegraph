class CatalogueErrorMessages:
    EPISODE_TITLE_MUST_BE_TRIMMED = (
        "Episode title cannot be empty or have leading/trailing whitespace."
    )
    EPISODE_REVIEWED_SUBTITLE_FILENAME_MUST_BE_SAFE = (
        "Episode reviewed subtitle filename must be a safe basename."
    )
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
    CATALOGUE_SCHEMA_VERSION_MUST_BE_SUPPORTED = (
        "Catalogue manifest schema version must be supported."
    )
    CATALOGUE_SERIES_MUST_BE_IMMUTABLE = (
        "Catalogue manifest series must be immutable."
    )
    CATALOGUE_MUST_CONTAIN_SERIES = (
        "Catalogue manifest must contain at least one series."
    )
    CATALOGUE_SERIES_IDS_MUST_BE_UNIQUE = (
        "Catalogue manifest series IDs must be unique."
    )
    CATALOGUE_SERIES_NAMES_MUST_BE_UNIQUE = (
        "Catalogue manifest series names must be unique."
    )
    CATALOGUE_SEASON_IDS_MUST_BE_GLOBALLY_UNIQUE = (
        "Catalogue manifest season IDs must be globally unique."
    )
    CATALOGUE_EPISODE_IDS_MUST_BE_GLOBALLY_UNIQUE = (
        "Catalogue manifest episode IDs must be globally unique."
    )
    CATALOGUE_MANIFEST_PATH_MUST_BE_FILE = (
        "Catalogue manifest path must identify a readable file."
    )
    CATALOGUE_MANIFEST_JSON_MUST_BE_VALID = (
        "Catalogue manifest must contain valid JSON."
    )
    CATALOGUE_MANIFEST_STRUCTURE_MUST_BE_VALID = (
        "Catalogue manifest structure and values must match schema version 1."
    )
