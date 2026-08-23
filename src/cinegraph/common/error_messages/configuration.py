class ConfigurationErrorMessages:
    QDRANT_LOCAL_PATH_REQUIRED = "Local Qdrant mode requires a configured storage path."
    QDRANT_REMOTE_URL_REQUIRED = "Remote Qdrant mode requires a configured URL."
    PRODUCTION_QDRANT_MUST_BE_REMOTE = "Production runtime must use remote Qdrant mode."
    QDRANT_COLLECTION_NAME_MUST_BE_TRIMMED = (
        "Runtime Qdrant collection name must be non-empty and trimmed."
    )
    PRODUCTION_DATABASE_MUST_BE_POSTGRES = (
        "Production runtime must use PostgreSQL with the Psycopg 3 driver."
    )
    DATABASE_URL_MUST_BE_TRIMMED = "Database URL must be non-empty and trimmed."
    DATABASE_URL_MUST_BE_VALID = "Database URL must be a valid SQLAlchemy URL."
    DATABASE_DIALECT_MUST_BE_SUPPORTED = (
        "Database URL must use the configured SQLite or PostgreSQL driver."
    )
    DATABASE_URL_MUST_BE_CONFIGURED = "Database URL must be configured before use."
    DATABASE_POOL_SETTINGS_MUST_BE_POSITIVE = (
        "Database pool settings must be positive integers."
    )
