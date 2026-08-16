class CorpusIngestionErrorMessages:
    REVIEW_LEDGER_MUST_BE_VALID = (
        "Reviewed subtitle ledger must match schema version 1."
    )
    REVIEW_LEDGER_RECORD_FILENAMES_MUST_BE_UNIQUE = (
        "Reviewed subtitle ledger filenames must be unique."
    )
    REVIEWED_SUBTITLE_MUST_MAP_TO_CATALOGUE = (
        "Every reviewed subtitle ledger record must map to one catalogue episode."
    )
    CATALOGUE_SUBTITLE_FILENAMES_MUST_BE_UNIQUE = (
        "Catalogue reviewed subtitle filenames must be unique."
    )
    REVIEWED_SUBTITLE_FILE_MUST_EXIST = (
        "Every reviewed subtitle ledger record must identify an existing file."
    )
    REVIEWED_SUBTITLE_HASH_MUST_MATCH_LEDGER = (
        "Reviewed subtitle content hash must match the review ledger."
    )
    INGESTED_SOURCE_HASH_MUST_MATCH_REVIEW_LEDGER = (
        "Ingested source content hash must match the verified review ledger."
    )
