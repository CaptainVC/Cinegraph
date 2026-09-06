class RetrievalErrorMessages:
    HYBRID_RETRIEVAL_CANDIDATE_LIMITS_INVALID = (
        "Hybrid retrieval candidate limits must be positive."
    )
    HYBRID_RETRIEVAL_OVERLAP_RATIO_INVALID = (
        "Hybrid retrieval overlap ratio must be between zero and one."
    )
    SEARCH_LIMIT_EXCEEDS_CONFIGURED_MAXIMUM = "Search limit exceeds the configured maximum."
    QDRANT_RESULT_MEMBER_SEGMENTS_MUST_BE_VALID = (
        "Qdrant result member segment IDs must be unique UUIDs."
    )
    QDRANT_RESULT_INDEX_REVISION_MUST_MATCH = (
        "Qdrant result index revision must match the current revision."
    )
    QDRANT_RESULT_SOURCE_STATUS_MUST_BE_ACTIVE = "Qdrant result source status must be active."
    QDRANT_RESULT_REVIEW_STATUS_MUST_BE_APPROVED = "Qdrant result review status must be approved."
    EMBEDDING_DENSE_DIMENSION_MUST_BE_POSITIVE = "Embedding dense dimension must be positive."
    EMBEDDING_BATCH_SIZE_MUST_BE_POSITIVE = "Embedding batch size must be positive."
    EMBEDDING_INFERENCE_THREADS_MUST_BE_POSITIVE = (
        "Embedding inference threads must be positive."
    )
    EMBEDDING_MODEL_NAME_MUST_BE_TRIMMED_NONEMPTY = (
        "Embedding model names must be non-empty and trimmed."
    )
    EMBEDDING_FALLBACK_INDEX_MUST_BE_NON_NEGATIVE = "Embedding fallback index must be non-negative."
    EMBEDDING_FALLBACK_VALUE_MUST_BE_FINITE_POSITIVE = (
        "Embedding fallback value must be finite and positive."
    )
    VECTOR_ENCODER_BACKEND_RESULT_CARDINALITY_MUST_MATCH = (
        "Vector encoder backend result cardinality must match the input batch."
    )
    VECTOR_ENCODER_DENSE_DIMENSION_MUST_MATCH = (
        "Vector encoder dense dimension must match the configured dimension."
    )
    VECTOR_ENCODER_BACKEND_RESULT_MUST_NOT_BE_EMPTY = (
        "Vector encoder backend result must not be empty."
    )
    DENSE_VECTOR_VALUES_MUST_BE_TUPLE = "Dense vector values must be a tuple."
    DENSE_VECTOR_VALUES_MUST_NOT_BE_EMPTY = "Dense vector values must not be empty."
    DENSE_VECTOR_VALUES_MUST_BE_NUMERIC = "Dense vector values must be numeric and not bool."
    DENSE_VECTOR_VALUES_MUST_BE_FINITE = "Dense vector values must be finite."
    SPARSE_VECTOR_INDICES_MUST_BE_TUPLE = "Sparse vector indices must be a tuple."
    SPARSE_VECTOR_VALUES_MUST_BE_TUPLE = "Sparse vector values must be a tuple."
    SPARSE_VECTOR_MUST_NOT_BE_EMPTY = "Sparse vector indices and values must not be empty."
    SPARSE_VECTOR_INDICES_AND_VALUES_MUST_MATCH = (
        "Sparse vector indices and values must have matching lengths."
    )
    SPARSE_VECTOR_INDICES_MUST_BE_NON_NEGATIVE_INTS = (
        "Sparse vector indices must be non-negative integers and not bool."
    )
    SPARSE_VECTOR_INDICES_MUST_BE_STRICTLY_INCREASING = (
        "Sparse vector indices must be strictly increasing."
    )
    SPARSE_VECTOR_VALUES_MUST_BE_NUMERIC = "Sparse vector values must be numeric and not bool."
    SPARSE_VECTOR_VALUES_MUST_BE_FINITE = "Sparse vector values must be finite."
    SPARSE_VECTOR_VALUES_MUST_BE_NONZERO = "Sparse vector values must be nonzero."
    HYBRID_VECTOR_DENSE_MUST_BE_DENSE_VECTOR = "Hybrid vector dense must be a DenseVector."
    HYBRID_VECTOR_SPARSE_MUST_BE_SPARSE_VECTOR = "Hybrid vector sparse must be a SparseVector."
    QUERY_VECTOR_MUST_CONTAIN_HYBRID_VECTOR = "Query vector must contain a HybridVector."
    DOCUMENT_VECTOR_MUST_CONTAIN_HYBRID_VECTOR = "Document vector must contain a HybridVector."
    SEARCH_LIMIT_MUST_BE_POSITIVE = "Search limit must be at least one."
    SEARCH_QUERY_MUST_BE_TRIMMED_NONEMPTY = "Search query must be non-empty and trimmed."
    CANDIDATE_EPISODES_MUST_BE_IMMUTABLE = (
        "Candidate episodes must be provided as an immutable tuple."
    )
    CANDIDATE_EPISODE_IDS_MUST_BE_UNIQUE = "Candidate episode IDs must be unique."
    EPISODE_VISIBILITY_SCOPE_SAFE_UNTIL_MS_MUST_BE_NON_NEGATIVE = (
        "Episode visibility scope safe_until_ms must be non-negative."
    )
    RETRIEVAL_SCOPE_EPISODE_SCOPES_MUST_BE_IMMUTABLE = (
        "Retrieval scope episode scopes must be immutable."
    )
    RETRIEVAL_SCOPE_EPISODES_MUST_MATCH_SERIES = "Retrieval scope episodes must match the series."
    RETRIEVAL_SCOPE_CANNOT_HAVE_DUPLICATE_EPISODES = (
        "Retrieval scope cannot contain duplicate episode IDs."
    )
    CANDIDATE_EPISODES_MUST_MATCH_SERIES = "Candidate episodes must match the requested series."
    QDRANT_RESULT_PAYLOAD_MUST_BE_COMPLETE = "Qdrant result payload must be present and complete."
    QDRANT_RESULT_IDS_MUST_BE_VALID = "Qdrant result IDs must be valid UUIDs."
    QDRANT_RESULT_GOVERNANCE_FIELDS_MUST_BE_VALID = (
        "Qdrant result language and rights status must be valid."
    )
    QDRANT_RESULT_SERIES_MUST_MATCH_SCOPE = "Qdrant result series must match the retrieval scope."
    QDRANT_RESULT_NUMERIC_FIELDS_MUST_BE_VALID = (
        "Qdrant result numeric fields must be valid integers."
    )
    QDRANT_RESULT_TEXT_MUST_BE_VALID = "Qdrant result text must be non-empty and trimmed."
    QDRANT_RESULT_SCORE_MUST_BE_FINITE = "Qdrant result score must be finite numeric data."
    QDRANT_RESULT_MUST_MATCH_VISIBILITY_SCOPE = (
        "Qdrant result must match an episode and time bound in the retrieval scope."
    )
    RETRIEVED_SEGMENT_IDS_MUST_BE_UUIDS = "Retrieved segment and source version IDs must be UUIDs."
    RETRIEVED_SEGMENT_EPISODE_MUST_BE_VALID = "Retrieved segment episode must be an EpisodeRef."
    RETRIEVED_SEGMENT_TIMING_MUST_BE_VALID = (
        "Retrieved segment timing must contain valid millisecond integers."
    )
    RETRIEVED_SEGMENT_TEXT_MUST_BE_VALID = "Retrieved segment text must be non-empty and trimmed."
    RETRIEVED_SEGMENT_GOVERNANCE_MUST_BE_VALID = (
        "Retrieved segment language and rights status must be valid."
    )
    RETRIEVED_SEGMENT_SCORE_MUST_BE_FINITE = "Retrieved segment score must be finite numeric data."
    VECTOR_INDEX_RESULT_COUNT_MUST_NOT_EXCEED_LIMIT = (
        "Vector index result count must not exceed the requested limit."
    )
    VECTOR_INDEX_RESULT_IDS_MUST_BE_UNIQUE = "Vector index result segment IDs must be unique."
    VECTOR_INDEX_RESULT_MUST_MATCH_SCOPE = (
        "Vector index result must match an episode and time bound in the retrieval scope."
    )
