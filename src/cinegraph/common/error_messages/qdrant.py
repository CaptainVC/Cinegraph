class QdrantErrorMessages:
    SOURCE_VERSION_REPLACEMENT_IDS_MUST_DIFFER = "New and retired source versions must differ."
    SOURCE_VERSION_POINT_MUST_MATCH_NEW = (
        "Every replacement point must belong to the new source version."
    )
    SOURCE_VERSION_POINTS_MUST_SHARE_EPISODE_AND_LANGUAGE = (
        "Episode-language replacement points must share one episode and language."
    )
    SOURCE_VERSION_POINTS_MUST_BE_UNIQUE = "Replacement chunk IDs must be unique."
    SOURCE_VERSION_POINT_REVISION_MUST_MATCH = (
        "Replacement point index revision must match the current revision."
    )
    SOURCE_VERSION_POINT_MEMBERS_MUST_BE_VALID = (
        "Replacement point member segment IDs must be non-empty and unique."
    )
    SOURCE_VERSION_IDS_MUST_BE_UUIDS = "Replacement source version IDs must be UUIDs."
    SOURCE_VERSION_POINT_DENSE_DIMENSION_MUST_MATCH = (
        "Replacement point dense dimension must match the Qdrant collection schema."
    )
    COLLECTION_NAME_MUST_BE_TRIMMED_NONEMPTY = (
        "Qdrant collection name must be non-empty and trimmed."
    )
    VECTOR_NAME_MUST_BE_TRIMMED_NONEMPTY = "Qdrant vector names must be non-empty and trimmed."
    DENSE_VECTOR_SIZE_MUST_BE_POSITIVE = "Qdrant dense vector size must be a positive integer."
    PAYLOAD_INDEX_DEFINITIONS_MUST_BE_IMMUTABLE = (
        "Qdrant payload index definitions must be immutable."
    )
    PAYLOAD_INDEX_FIELDS_MUST_BE_UNIQUE = "Qdrant payload index fields must be unique."
    COLLECTION_STATUS_MUST_BE_GREEN = "Qdrant transcript collection status must be green."
    DENSE_VECTOR_CONFIGURATION_MUST_MATCH = (
        "Qdrant dense vector configuration must match the configured schema."
    )
    SPARSE_VECTOR_CONFIGURATION_MUST_MATCH = (
        "Qdrant sparse vector configuration must match the configured schema."
    )
    PAYLOAD_INDEX_CONFIGURATION_MUST_MATCH = (
        "Qdrant payload index configuration must match the configured schema."
    )
