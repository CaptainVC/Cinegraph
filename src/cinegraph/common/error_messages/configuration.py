class ConfigurationErrorMessages:
    QDRANT_LOCAL_PATH_REQUIRED = (
        "Local Qdrant mode requires a configured storage path."
    )
    QDRANT_REMOTE_URL_REQUIRED = (
        "Remote Qdrant mode requires a configured URL."
    )
    PRODUCTION_QDRANT_MUST_BE_REMOTE = (
        "Production runtime must use remote Qdrant mode."
    )
    QDRANT_COLLECTION_NAME_MUST_BE_TRIMMED = (
        "Runtime Qdrant collection name must be non-empty and trimmed."
    )
