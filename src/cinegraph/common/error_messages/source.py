class SourceErrorMessages:
    SOURCE_DOCUMENT_ID_METADATA_CONFLICT = (
        "A source document ID cannot be reused with different metadata."
    )
    ACTIVE_SOURCE_VERSION_CONFLICT = (
        "The active source version changed before persistence."
    )
    TRANSCRIPT_SEGMENT_SOURCE_VERSION_MISMATCH = (
        "Every persisted transcript segment must reference its source version."
    )
    SOURCE_DOCUMENT_TITLE_MUST_BE_TRIMMED = (
        "Source document title cannot be empty or have leading/trailing whitespace."
    )
    SOURCE_DOCUMENT_ORIGIN_MUST_BE_TRIMMED = (
        "Source document origin cannot be empty or have leading/trailing whitespace."
    )
    SOURCE_VERSION_CONTENT_HASH_MUST_BE_SHA256 = (
        "Source version content hash must be a lowercase SHA-256 hexadecimal digest."
    )
    SOURCE_VERSION_ACQUIRED_AT_MUST_BE_TIMEZONE_AWARE = (
        "Source version acquired_at must be timezone-aware."
    )
    SOURCE_VERSION_REVIEWED_AT_MUST_BE_TIMEZONE_AWARE = (
        "Source version reviewed_at must be timezone-aware when provided."
    )
    SOURCE_VERSION_REVIEWER_MUST_BE_TRIMMED = (
        "Source version reviewed_by cannot be empty or have leading/trailing whitespace."
    )
    SOURCE_VERSION_REVIEWED_REQUIRES_REVIEW_METADATA = (
        "A final source review requires reviewed_by and reviewed_at."
    )
    SOURCE_VERSION_NON_REVIEWED_CANNOT_HAVE_REVIEW_METADATA = (
        "A non-reviewed source version cannot have review metadata."
    )
    SOURCE_VERSION_PARENT_CANNOT_EQUAL_SELF = (
        "A source version cannot name itself as its parent version."
    )
    SOURCE_VERSION_NOT_FOUND = "Source version was not found: {source_version_id}"
    SOURCE_VERSION_REVIEW_REQUIRES_FINAL_DECISION = (
        "Source versions can only be reviewed or rejected."
    )
    EPISODE_SUMMARY_SOURCE_VERSION_MISMATCH = (
        "Episode summary must reference the persisted source version."
    )
    MEDIAWIKI_PAGE_TITLE_MUST_BE_TRIMMED = (
        "MediaWiki page title cannot be empty or have leading/trailing whitespace."
    )
    MEDIAWIKI_PAGE_NOT_FOUND = (
        "MediaWiki page was not found: {page_title}"
    )
    MEDIAWIKI_RESPONSE_MISSING_REQUIRED_DATA = (
        "MediaWiki response is missing required page, revision, URL, or extract data."
    )
    MEDIAWIKI_PAGE_EXTRACT_MUST_BE_TRIMMED = (
        "MediaWiki page extract cannot be empty or have leading/trailing whitespace."
    )
    EPISODE_SUMMARY_CANONICAL_URL_MUST_BE_TRIMMED = (
    "Episode summary canonical_url cannot be empty or have leading/trailing whitespace."
    )
    EPISODE_SUMMARY_REVISION_ID_MUST_BE_POSITIVE = (
        "Episode summary revision_id must be a positive integer."
    )
    EPISODE_SUMMARY_REVISION_TIMESTAMP_MUST_BE_TIMEZONE_AWARE = (
        "Episode summary revision_timestamp must be timezone-aware."
    )
    EPISODE_SUMMARY_ATTRIBUTION_MUST_BE_TRIMMED = (
        "Episode summary attribution cannot be empty or have leading/trailing whitespace."
    )
    METADATA_URL_MUST_BE_HTTP = "Metadata URLs must use http or https."
    METADATA_VALUE_MUST_BE_TRIMMED = "Metadata text values cannot be empty or untrimmed."
    METADATA_PROVIDER_ID_MUST_BE_POSITIVE = "Metadata provider IDs must be positive integers."
    METADATA_DIMENSIONS_MUST_BE_POSITIVE = "Artwork dimensions must be positive when provided."
    METADATA_RETRIEVED_AT_MUST_BE_TIMEZONE_AWARE = "Metadata retrieved_at must be timezone-aware."
    METADATA_SHOW_TITLE_MISMATCH = "TVmaze show title does not exactly match the catalogue title."
    TVMAZE_RESPONSE_MALFORMED = "TVmaze response is missing required metadata."
    TVMAZE_DUPLICATE_EPISODE = "TVmaze returned duplicate episodes for a catalogue position."
    TVMAZE_EPISODE_NOT_FOUND = "TVmaze did not return a requested catalogue episode."
    TVMAZE_HTTP_ERROR = "TVmaze request failed: {detail}"
    SERIES_METADATA_SOURCE_VERSION_MISMATCH = (
        "Series metadata must reference the persisted source version."
    )
    SERIES_METADATA_SOURCE_DOCUMENT_MUST_BE_METADATA = (
        "Series metadata source documents must use the METADATA source kind."
    )
    SERIES_METADATA_EPISODE_SCOPE_MUST_BE_NON_EMPTY = (
        "Series metadata episode scope cannot be empty."
    )
    SERIES_METADATA_EPISODE_SCOPE_MUST_BE_IMMUTABLE = (
        "Series metadata episode scope must be an immutable tuple."
    )
    SERIES_METADATA_EPISODE_SCOPE_SERIES_MISMATCH = (
        "Every series metadata episode must belong to the command series."
    )
    SERIES_METADATA_RIGHTS_MUST_BE_ALLOWED = (
        "TVmaze series metadata requires ALLOWED rights status."
    )
    TVMAZE_SHOW_ID_MUST_BE_POSITIVE = "TVmaze show ID must be a positive integer."
    TVMAZE_SHOW_ID_MISMATCH = "TVmaze response show ID does not match the requested ID."
    TVMAZE_DUPLICATE_SEASON = "TVmaze returned duplicate seasons for a requested season number."
    TVMAZE_SEASON_NOT_FOUND = "TVmaze did not return a requested season."
    TVMAZE_EPISODE_SCOPE_MUST_BE_NON_EMPTY = "TVmaze episode scope cannot be empty."
    TVMAZE_EPISODE_SERIES_MISMATCH = "Every requested episode must belong to the supplied series."
    TVMAZE_DUPLICATE_REQUESTED_EPISODE = "The requested episode scope contains duplicate positions."
