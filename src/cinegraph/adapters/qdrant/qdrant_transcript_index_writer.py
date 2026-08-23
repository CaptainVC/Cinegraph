from typing import Protocol
from uuid import UUID

from qdrant_client.http import models

from cinegraph.common.error_messages import QdrantErrorMessages
from cinegraph.config.qdrant import (
    QDRANT_CHUNK_ORDINAL_FIELD,
    QDRANT_END_MS_FIELD,
    QDRANT_EPISODE_ID_FIELD,
    QDRANT_EPISODE_NUMBER_FIELD,
    QDRANT_INDEX_REVISION_FIELD,
    QDRANT_LANGUAGE_FIELD,
    QDRANT_MEMBER_SEGMENT_IDS_FIELD,
    QDRANT_REVIEW_STATUS_FIELD,
    QDRANT_RIGHTS_STATUS_FIELD,
    QDRANT_SEASON_ID_FIELD,
    QDRANT_SEASON_NUMBER_FIELD,
    QDRANT_SERIES_ID_FIELD,
    QDRANT_SOURCE_STATUS_FIELD,
    QDRANT_SOURCE_VERSION_ID_FIELD,
    QDRANT_START_MS_FIELD,
    QDRANT_TEXT_FIELD,
    QdrantTranscriptCollectionSchema,
)
from cinegraph.config.transcript_chunking import TRANSCRIPT_INDEX_REVISION
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.ports.retrieval.transcript_index_writer import (
    TranscriptIndexPoint,
    TranscriptIndexWriter,
)


class QdrantWriteClient(Protocol):
    # Define the narrow Qdrant write surface required by the transcript writer.
    def upsert(
        self,
        *,
        collection_name: str,
        points: list[models.PointStruct],
        wait: bool,
    ) -> object: ...

    def delete(self, *, collection_name: str, points_selector: object, wait: bool) -> object: ...


class QdrantTranscriptIndexWriter(TranscriptIndexWriter):
    # Store the client and collection used for transcript point writes.
    def __init__(self, client: QdrantWriteClient, schema: QdrantTranscriptCollectionSchema) -> None:
        self._client = client
        self._schema = schema

    # Persist one ordered batch of transcript index points in Qdrant.
    def replace_source_version(
        self,
        new_source_version_id: UUID,
        retired_source_version_id: UUID | None,
        points: tuple[TranscriptIndexPoint, ...],
    ) -> None:
        if not isinstance(new_source_version_id, UUID) or (
            retired_source_version_id is not None
            and not isinstance(retired_source_version_id, UUID)
        ):
            raise InvalidModelError(QdrantErrorMessages.SOURCE_VERSION_IDS_MUST_BE_UUIDS)
        if retired_source_version_id == new_source_version_id:
            raise InvalidModelError(QdrantErrorMessages.SOURCE_VERSION_REPLACEMENT_IDS_MUST_DIFFER)
        chunk_ids = tuple(point.chunk_id for point in points)
        if len(set(chunk_ids)) != len(chunk_ids):
            raise InvalidModelError(QdrantErrorMessages.SOURCE_VERSION_POINTS_MUST_BE_UNIQUE)
        if any(point.payload.source_version_id != new_source_version_id for point in points):
            raise InvalidModelError(QdrantErrorMessages.SOURCE_VERSION_POINT_MUST_MATCH_NEW)
        if any(point.payload.index_revision != TRANSCRIPT_INDEX_REVISION for point in points):
            raise InvalidModelError(QdrantErrorMessages.SOURCE_VERSION_POINT_REVISION_MUST_MATCH)
        if any(
            len(point.vector.vector.dense.values) != self._schema.dense_vector_size
            for point in points
        ):
            raise InvalidModelError(
                QdrantErrorMessages.SOURCE_VERSION_POINT_DENSE_DIMENSION_MUST_MATCH
            )
        if any(
            not point.payload.member_segment_ids
            or any(not isinstance(item, UUID) for item in point.payload.member_segment_ids)
            or len(set(point.payload.member_segment_ids)) != len(point.payload.member_segment_ids)
            for point in points
        ):
            raise InvalidModelError(QdrantErrorMessages.SOURCE_VERSION_POINT_MEMBERS_MUST_BE_VALID)
        if points:
            self._upsert(points)
        if retired_source_version_id is not None:
            self._delete_retired(retired_source_version_id)

    def _upsert(self, points: tuple[TranscriptIndexPoint, ...]) -> None:
        # Convert each immutable domain point into Qdrant's named hybrid vector.
        qdrant_points = [
            models.PointStruct(
                id=str(point.chunk_id),
                vector={
                    self._schema.dense_vector_name: list(point.vector.vector.dense.values),
                    self._schema.sparse_vector_name: models.SparseVector(
                        indices=list(point.vector.vector.sparse.indices),
                        values=list(point.vector.vector.sparse.values),
                    ),
                },
                payload={
                    QDRANT_SOURCE_VERSION_ID_FIELD: str(point.payload.source_version_id),
                    QDRANT_SERIES_ID_FIELD: str(point.payload.series_id),
                    QDRANT_SEASON_ID_FIELD: str(point.payload.season_id),
                    QDRANT_EPISODE_ID_FIELD: str(point.payload.episode_id),
                    QDRANT_SEASON_NUMBER_FIELD: int(point.payload.season_number),
                    QDRANT_EPISODE_NUMBER_FIELD: int(point.payload.episode_number),
                    QDRANT_START_MS_FIELD: int(point.payload.start_ms),
                    QDRANT_END_MS_FIELD: int(point.payload.end_ms),
                    QDRANT_TEXT_FIELD: point.payload.text,
                    QDRANT_LANGUAGE_FIELD: point.payload.language.value,
                    QDRANT_RIGHTS_STATUS_FIELD: point.payload.rights_status.value,
                    QDRANT_SOURCE_STATUS_FIELD: point.payload.source_status.value,
                    QDRANT_REVIEW_STATUS_FIELD: point.payload.review_status.value,
                    QDRANT_INDEX_REVISION_FIELD: point.payload.index_revision,
                    QDRANT_MEMBER_SEGMENT_IDS_FIELD: [
                        str(item) for item in point.payload.member_segment_ids
                    ],
                    QDRANT_CHUNK_ORDINAL_FIELD: int(point.payload.chunk_ordinal),
                },
            )
            for point in points
        ]

        # Wait for Qdrant to acknowledge the complete ordered batch.
        self._client.upsert(
            collection_name=self._schema.collection_name,
            points=qdrant_points,
            wait=True,
        )

    def _delete_retired(self, retired_source_version_id: UUID) -> None:
        if retired_source_version_id is not None:
            selector = models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key=QDRANT_SOURCE_VERSION_ID_FIELD,
                            match=models.MatchValue(value=str(retired_source_version_id)),
                        )
                    ]
                )
            )
            self._client.delete(
                collection_name=self._schema.collection_name,
                points_selector=selector,
                wait=True,
            )
