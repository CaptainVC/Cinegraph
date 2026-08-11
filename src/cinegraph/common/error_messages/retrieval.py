class RetrievalErrorMessages:
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
    SPARSE_VECTOR_VALUES_MUST_BE_NUMERIC = (
        "Sparse vector values must be numeric and not bool."
    )
    SPARSE_VECTOR_VALUES_MUST_BE_FINITE = "Sparse vector values must be finite."
    SPARSE_VECTOR_VALUES_MUST_BE_NONZERO = "Sparse vector values must be nonzero."
    HYBRID_VECTOR_DENSE_MUST_BE_DENSE_VECTOR = "Hybrid vector dense must be a DenseVector."
    HYBRID_VECTOR_SPARSE_MUST_BE_SPARSE_VECTOR = "Hybrid vector sparse must be a SparseVector."
    QUERY_VECTOR_MUST_CONTAIN_HYBRID_VECTOR = "Query vector must contain a HybridVector."
    DOCUMENT_VECTOR_MUST_CONTAIN_HYBRID_VECTOR = (
        "Document vector must contain a HybridVector."
    )
    SEARCH_LIMIT_MUST_BE_POSITIVE = "Search limit must be at least one."
    EPISODE_VISIBILITY_SCOPE_SAFE_UNTIL_MS_MUST_BE_NON_NEGATIVE = (
        "Episode visibility scope safe_until_ms must be non-negative."
    )
    RETRIEVAL_SCOPE_EPISODE_SCOPES_MUST_BE_IMMUTABLE = (
        "Retrieval scope episode scopes must be immutable."
    )
    RETRIEVAL_SCOPE_EPISODES_MUST_MATCH_SERIES = (
        "Retrieval scope episodes must match the series."
    )
    RETRIEVAL_SCOPE_CANNOT_HAVE_DUPLICATE_EPISODES = (
        "Retrieval scope cannot contain duplicate episode IDs."
    )
    CANDIDATE_EPISODES_MUST_MATCH_SERIES = (
        "Candidate episodes must match the requested series."
    )
    QDRANT_RESULT_PAYLOAD_MUST_BE_COMPLETE = (
        "Qdrant result payload must be present and complete."
    )
    QDRANT_RESULT_IDS_MUST_BE_VALID = "Qdrant result IDs must be valid UUIDs."
    QDRANT_RESULT_SERIES_MUST_MATCH_SCOPE = (
        "Qdrant result series must match the retrieval scope."
    )
    QDRANT_RESULT_NUMERIC_FIELDS_MUST_BE_VALID = (
        "Qdrant result numeric fields must be valid integers."
    )
    QDRANT_RESULT_TEXT_MUST_BE_VALID = "Qdrant result text must be non-empty and trimmed."
    QDRANT_RESULT_SCORE_MUST_BE_FINITE = "Qdrant result score must be finite numeric data."
