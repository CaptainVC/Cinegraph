from typing import Protocol

from qdrant_client.http import models

from cinegraph.config.qdrant import (
    DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA,
    QDRANT_END_MS_FIELD,
    QDRANT_EPISODE_ID_FIELD,
    QDRANT_EPISODE_NUMBER_FIELD,
    QDRANT_LANGUAGE_FIELD,
    QDRANT_REVIEW_STATUS_FIELD,
    QDRANT_RIGHTS_STATUS_FIELD,
    QDRANT_SEASON_ID_FIELD,
    QDRANT_SEASON_NUMBER_FIELD,
    QDRANT_SERIES_ID_FIELD,
    QDRANT_SOURCE_STATUS_FIELD,
    QDRANT_SOURCE_VERSION_ID_FIELD,
    QDRANT_START_MS_FIELD,
    QDRANT_TEXT_FIELD,
)
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


class QdrantTranscriptIndexWriter(TranscriptIndexWriter):
    # Store the client and collection used for transcript point writes.
    def __init__(self, client: QdrantWriteClient, collection_name: str) -> None:
        self._client = client
        self._collection_name = collection_name

    # Persist one ordered batch of transcript index points in Qdrant.
    def upsert(self, points: tuple[TranscriptIndexPoint, ...]) -> None:
        if not points:
            return

        # Convert each immutable domain point into Qdrant's named hybrid vector.
        qdrant_points = [
            models.PointStruct(
                id=str(point.segment_id),
                vector={
                    DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA.dense_vector_name: list(
                        point.vector.vector.dense.values
                    ),
                    DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA.sparse_vector_name: models.SparseVector(
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
                },
            )
            for point in points
        ]

        # Wait for Qdrant to acknowledge the complete ordered batch.
        self._client.upsert(
            collection_name=self._collection_name,
            points=qdrant_points,
            wait=True,
        )
