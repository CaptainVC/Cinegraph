class QdrantErrorMessages:
    COLLECTION_NAME_MUST_BE_TRIMMED_NONEMPTY = (
        "Qdrant collection name must be non-empty and trimmed."
    )
    VECTOR_NAME_MUST_BE_TRIMMED_NONEMPTY = (
        "Qdrant vector names must be non-empty and trimmed."
    )
    DENSE_VECTOR_SIZE_MUST_BE_POSITIVE = (
        "Qdrant dense vector size must be a positive integer."
    )
    PAYLOAD_INDEX_DEFINITIONS_MUST_BE_IMMUTABLE = (
        "Qdrant payload index definitions must be immutable."
    )
    PAYLOAD_INDEX_FIELDS_MUST_BE_UNIQUE = (
        "Qdrant payload index fields must be unique."
    )
    COLLECTION_STATUS_MUST_BE_GREEN = (
        "Qdrant transcript collection status must be green."
    )
    DENSE_VECTOR_CONFIGURATION_MUST_MATCH = (
        "Qdrant dense vector configuration must match the configured schema."
    )
    SPARSE_VECTOR_CONFIGURATION_MUST_MATCH = (
        "Qdrant sparse vector configuration must match the configured schema."
    )
    PAYLOAD_INDEX_CONFIGURATION_MUST_MATCH = (
        "Qdrant payload index configuration must match the configured schema."
    )
