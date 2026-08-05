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
