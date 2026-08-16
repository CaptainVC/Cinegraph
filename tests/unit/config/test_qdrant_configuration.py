from dataclasses import replace

import pytest

from cinegraph.common.error_messages import QdrantErrorMessages
from cinegraph.config import DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA


def test_default_qdrant_schema_has_unique_filter_indexes() -> None:
    schema = DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA

    fields = tuple(item.field_name for item in schema.payload_indexes)

    assert schema.collection_name == "transcript_segments"
    assert schema.dense_vector_size == 384
    assert len(fields) == len(set(fields))
    assert {"series_id", "episode_id", "end_ms", "source_status", "review_status"} <= set(
        fields
    )


def test_invalid_dense_vector_size_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match=QdrantErrorMessages.DENSE_VECTOR_SIZE_MUST_BE_POSITIVE,
    ):
        replace(DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA, dense_vector_size=0)


def test_duplicate_payload_index_field_is_rejected() -> None:
    schema = DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA

    with pytest.raises(
        ValueError,
        match=QdrantErrorMessages.PAYLOAD_INDEX_FIELDS_MUST_BE_UNIQUE,
    ):
        replace(schema, payload_indexes=(schema.payload_indexes[0],) * 2)
