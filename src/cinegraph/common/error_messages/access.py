class AccessErrorMessages:
    CORPUS_ACCESS_MODE_MUST_BE_VALID = "Corpus-access mode must be valid."
    CORPUS_ACCESS_UNRESTRICTED_MUST_BE_BOOLEAN = (
        "Corpus-access unrestricted flag must be boolean."
    )
    CORPUS_SEASON_SERIES_ID_MUST_BE_UUID = (
        "Corpus-access series identifier must be a UUID."
    )
    CORPUS_SEASON_NUMBER_MUST_BE_POSITIVE = (
        "Corpus-access season number must be positive."
    )
    CORPUS_SCOPE_REVISION_MUST_BE_NONEMPTY = (
        "Corpus-access scope revision must be nonempty and trimmed."
    )
    CORPUS_SCOPE_ALLOWED_SEASONS_MUST_BE_IMMUTABLE = (
        "Corpus-access allowed seasons must be an immutable frozenset."
    )
    CORPUS_SCOPE_ALLOWED_SEASONS_MUST_BE_VALID = (
        "Corpus-access allowed seasons must contain valid season grants."
    )
    CORPUS_ACCESS_DENIED = "Requested content is unavailable for this access scope."
    GUEST_CORPUS_SCOPE_CANNOT_BE_UNRESTRICTED = (
        "Guest corpus access cannot be unrestricted."
    )
    GUEST_CORPUS_SCOPE_REQUIRES_ALLOWED_SEASONS = (
        "Guest corpus access requires at least one allowed season."
    )
