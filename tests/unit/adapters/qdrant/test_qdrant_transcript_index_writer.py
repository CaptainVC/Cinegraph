from typing import Any
from uuid import UUID, uuid4

from qdrant_client.http import models
from tests.factories import make_episode_ref

from cinegraph.adapters.qdrant.qdrant_transcript_index_writer import (
    QdrantTranscriptIndexWriter,
)
from cinegraph.domain.enums.enum import (
    Language,
    RightsStatus,
    SourceReviewStatus,
    SourceVersionStatus,
)
from cinegraph.domain.retrieval.vector_data import (
    DenseVector,
    DocumentVector,
    HybridVector,
    SparseVector,
)
from cinegraph.ports.retrieval import TranscriptIndexPayload, TranscriptIndexPoint

COLLECTION_NAME = "transcript_segments"
SEGMENT_ID = UUID("00000000-0000-0000-0000-000000002001")
SOURCE_VERSION_ID = UUID("00000000-0000-0000-0000-000000000701")


class FakeQdrantClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result = models.UpdateResult(operation_id=7, status=models.UpdateStatus.COMPLETED)

    def upsert(self, **kwargs: Any) -> models.UpdateResult:
        self.calls.append(kwargs)
        return self.result


def make_document_vector(*, offset: float = 0.0) -> DocumentVector:
    return DocumentVector(
        vector=HybridVector(
            dense=DenseVector(values=(0.1 + offset, 0.2 + offset, 0.3 + offset)),
            sparse=SparseVector(indices=(2, 7), values=(0.4 + offset, 0.8 + offset)),
        )
    )


def make_point(
    *,
    segment_id: UUID = SEGMENT_ID,
    text: str = "Claire asks about dinner",
    offset: float = 0.0,
) -> TranscriptIndexPoint:
    episode = make_episode_ref(season_number=2, episode_number=3)
    return TranscriptIndexPoint(
        segment_id=segment_id,
        vector=make_document_vector(offset=offset),
        payload=TranscriptIndexPayload(
            source_version_id=SOURCE_VERSION_ID,
            series_id=episode.series_id,
            season_id=episode.season_id,
            episode_id=episode.episode_id,
            season_number=episode.position.season_number,
            episode_number=episode.position.episode_number,
            start_ms=1_000,
            end_ms=1_500,
            text=text,
            language=Language.ENGLISH,
            rights_status=RightsStatus.ALLOWED,
            source_status=SourceVersionStatus.ACTIVE,
            review_status=SourceReviewStatus.REVIEWED,
        ),
    )


def test_empty_points_return_without_calling_qdrant() -> None:
    client = FakeQdrantClient()

    QdrantTranscriptIndexWriter(client, COLLECTION_NAME).upsert(())

    assert client.calls == []


def test_one_point_maps_hybrid_vectors_id_and_exact_payload() -> None:
    client = FakeQdrantClient()
    point = make_point()

    result = QdrantTranscriptIndexWriter(client, COLLECTION_NAME).upsert((point,))

    assert result is None
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["collection_name"] == COLLECTION_NAME
    assert call["wait"] is True
    qdrant_point = call["points"][0]
    assert qdrant_point.id == str(point.segment_id)
    assert qdrant_point.vector == {
        "dense": [0.1, 0.2, 0.3],
        "sparse": models.SparseVector(indices=[2, 7], values=[0.4, 0.8]),
    }
    assert qdrant_point.payload == {
        "source_version_id": str(SOURCE_VERSION_ID),
        "series_id": str(point.payload.series_id),
        "season_id": str(point.payload.season_id),
        "episode_id": str(point.payload.episode_id),
        "season_number": 2,
        "episode_number": 3,
        "start_ms": 1_000,
        "end_ms": 1_500,
        "text": "Claire asks about dinner",
        "language": Language.ENGLISH.value,
        "rights_status": RightsStatus.ALLOWED.value,
        "source_status": SourceVersionStatus.ACTIVE.value,
        "review_status": SourceReviewStatus.REVIEWED.value,
    }


def test_multiple_points_use_one_call_and_preserve_input_order() -> None:
    client = FakeQdrantClient()
    first = make_point(segment_id=uuid4(), text="First line")
    second = make_point(segment_id=uuid4(), text="Second line", offset=0.1)

    QdrantTranscriptIndexWriter(client, COLLECTION_NAME).upsert((first, second))

    assert len(client.calls) == 1
    assert [point.id for point in client.calls[0]["points"]] == [
        str(first.segment_id),
        str(second.segment_id),
    ]
    assert [point.payload["text"] for point in client.calls[0]["points"]] == [
        first.payload.text,
        second.payload.text,
    ]
