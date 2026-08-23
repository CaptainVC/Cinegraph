from dataclasses import dataclass

from qdrant_client.http import models

from cinegraph.common.error_messages import QdrantErrorMessages

QDRANT_SOURCE_VERSION_ID_FIELD = "source_version_id"
QDRANT_SERIES_ID_FIELD = "series_id"
QDRANT_SEASON_ID_FIELD = "season_id"
QDRANT_EPISODE_ID_FIELD = "episode_id"
QDRANT_SEASON_NUMBER_FIELD = "season_number"
QDRANT_EPISODE_NUMBER_FIELD = "episode_number"
QDRANT_START_MS_FIELD = "start_ms"
QDRANT_END_MS_FIELD = "end_ms"
QDRANT_TEXT_FIELD = "text"
QDRANT_LANGUAGE_FIELD = "language"
QDRANT_RIGHTS_STATUS_FIELD = "rights_status"
QDRANT_SOURCE_STATUS_FIELD = "source_status"
QDRANT_REVIEW_STATUS_FIELD = "review_status"

QDRANT_TRANSCRIPT_REQUIRED_PAYLOAD_FIELDS = frozenset(
    {
        QDRANT_SOURCE_VERSION_ID_FIELD,
        QDRANT_SERIES_ID_FIELD,
        QDRANT_SEASON_ID_FIELD,
        QDRANT_EPISODE_ID_FIELD,
        QDRANT_SEASON_NUMBER_FIELD,
        QDRANT_EPISODE_NUMBER_FIELD,
        QDRANT_START_MS_FIELD,
        QDRANT_END_MS_FIELD,
        QDRANT_TEXT_FIELD,
        QDRANT_LANGUAGE_FIELD,
        QDRANT_RIGHTS_STATUS_FIELD,
    }
)


@dataclass(frozen=True, slots=True)
class QdrantPayloadIndexDefinition:
    field_name: str
    field_schema: models.PayloadSchemaType


@dataclass(frozen=True, slots=True)
class QdrantTranscriptCollectionSchema:
    collection_name: str
    dense_vector_name: str
    sparse_vector_name: str
    dense_vector_size: int
    distance: models.Distance
    on_disk_payload: bool
    payload_indexes: tuple[QdrantPayloadIndexDefinition, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.collection_name, str)
            or not self.collection_name
            or self.collection_name.strip() != self.collection_name
        ):
            raise ValueError(
                QdrantErrorMessages.COLLECTION_NAME_MUST_BE_TRIMMED_NONEMPTY
            )
        if any(
            not isinstance(name, str) or not name or name.strip() != name
            for name in (self.dense_vector_name, self.sparse_vector_name)
        ):
            raise ValueError(QdrantErrorMessages.VECTOR_NAME_MUST_BE_TRIMMED_NONEMPTY)
        if (
            isinstance(self.dense_vector_size, bool)
            or not isinstance(self.dense_vector_size, int)
            or self.dense_vector_size < 1
        ):
            raise ValueError(QdrantErrorMessages.DENSE_VECTOR_SIZE_MUST_BE_POSITIVE)
        if not isinstance(self.payload_indexes, tuple):
            raise ValueError(
                QdrantErrorMessages.PAYLOAD_INDEX_DEFINITIONS_MUST_BE_IMMUTABLE
            )
        fields = tuple(item.field_name for item in self.payload_indexes)
        if len(set(fields)) != len(fields):
            raise ValueError(QdrantErrorMessages.PAYLOAD_INDEX_FIELDS_MUST_BE_UNIQUE)


DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA = QdrantTranscriptCollectionSchema(
    collection_name="transcript_segments",
    dense_vector_name="dense",
    sparse_vector_name="sparse",
    dense_vector_size=384,
    distance=models.Distance.COSINE,
    on_disk_payload=True,
    payload_indexes=(
        QdrantPayloadIndexDefinition(
            QDRANT_SOURCE_VERSION_ID_FIELD,
            models.PayloadSchemaType.KEYWORD,
        ),
        QdrantPayloadIndexDefinition(
            QDRANT_SERIES_ID_FIELD,
            models.PayloadSchemaType.KEYWORD,
        ),
        QdrantPayloadIndexDefinition(
            QDRANT_SEASON_ID_FIELD,
            models.PayloadSchemaType.KEYWORD,
        ),
        QdrantPayloadIndexDefinition(
            QDRANT_EPISODE_ID_FIELD,
            models.PayloadSchemaType.KEYWORD,
        ),
        QdrantPayloadIndexDefinition(
            QDRANT_SEASON_NUMBER_FIELD,
            models.PayloadSchemaType.INTEGER,
        ),
        QdrantPayloadIndexDefinition(
            QDRANT_EPISODE_NUMBER_FIELD,
            models.PayloadSchemaType.INTEGER,
        ),
        QdrantPayloadIndexDefinition(
            QDRANT_START_MS_FIELD,
            models.PayloadSchemaType.INTEGER,
        ),
        QdrantPayloadIndexDefinition(
            QDRANT_END_MS_FIELD,
            models.PayloadSchemaType.INTEGER,
        ),
        QdrantPayloadIndexDefinition(
            QDRANT_LANGUAGE_FIELD,
            models.PayloadSchemaType.KEYWORD,
        ),
        QdrantPayloadIndexDefinition(
            QDRANT_RIGHTS_STATUS_FIELD,
            models.PayloadSchemaType.KEYWORD,
        ),
        QdrantPayloadIndexDefinition(
            QDRANT_SOURCE_STATUS_FIELD,
            models.PayloadSchemaType.KEYWORD,
        ),
        QdrantPayloadIndexDefinition(
            QDRANT_REVIEW_STATUS_FIELD,
            models.PayloadSchemaType.KEYWORD,
        ),
    ),
)
