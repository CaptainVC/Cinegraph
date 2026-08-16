from typing import Protocol

from qdrant_client.http import models

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
                    "dense": list(point.vector.vector.dense.values),
                    "sparse": models.SparseVector(
                        indices=list(point.vector.vector.sparse.indices),
                        values=list(point.vector.vector.sparse.values),
                    ),
                },
                payload={
                    "source_version_id": str(point.payload.source_version_id),
                    "series_id": str(point.payload.series_id),
                    "season_id": str(point.payload.season_id),
                    "episode_id": str(point.payload.episode_id),
                    "season_number": int(point.payload.season_number),
                    "episode_number": int(point.payload.episode_number),
                    "start_ms": int(point.payload.start_ms),
                    "end_ms": int(point.payload.end_ms),
                    "text": point.payload.text,
                    "language": point.payload.language.value,
                    "rights_status": point.payload.rights_status.value,
                    "source_status": point.payload.source_status.value,
                    "review_status": point.payload.review_status.value,
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
