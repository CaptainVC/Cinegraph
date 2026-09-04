from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

import pytest
from qdrant_client.http import models
from tests.factories import make_episode_ref

from cinegraph.adapters.qdrant.qdrant_transcript_index_writer import (
    QdrantTranscriptIndexWriter,
    QdrantTranscriptReplacementMode,
)
from cinegraph.common.error_messages import QdrantErrorMessages
from cinegraph.config import DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA
from cinegraph.config.transcript_chunking import TRANSCRIPT_INDEX_REVISION
from cinegraph.domain.enums.enum import (
    Language,
    RightsStatus,
    SourceReviewStatus,
    SourceVersionStatus,
)
from cinegraph.domain.exceptions.errors import InvalidModelError
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
SCHEMA = replace(DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA, dense_vector_size=3)


class FakeQdrantClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result = models.UpdateResult(operation_id=7, status=models.UpdateStatus.COMPLETED)

    def upsert(self, **kwargs: Any) -> models.UpdateResult:
        self.calls.append(kwargs)
        return self.result

    def delete(self, **kwargs: Any) -> models.UpdateResult:
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
        chunk_id=segment_id,
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
            member_segment_ids=(segment_id,),
            chunk_ordinal=0,
            index_revision=TRANSCRIPT_INDEX_REVISION,
        ),
    )


def test_empty_points_return_without_calling_qdrant() -> None:
    client = FakeQdrantClient()

    QdrantTranscriptIndexWriter(client, SCHEMA).replace_source_version(SOURCE_VERSION_ID, None, ())

    assert client.calls == []


def test_one_point_maps_hybrid_vectors_id_and_exact_payload() -> None:
    client = FakeQdrantClient()
    point = make_point()

    result = QdrantTranscriptIndexWriter(client, SCHEMA).replace_source_version(
        SOURCE_VERSION_ID, None, (point,)
    )

    assert result is None
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["collection_name"] == COLLECTION_NAME
    assert call["wait"] is True
    qdrant_point = call["points"][0]
    assert qdrant_point.id == str(point.chunk_id)
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
        "index_revision": TRANSCRIPT_INDEX_REVISION,
        "member_segment_ids": [str(point.chunk_id)],
        "chunk_ordinal": 0,
    }


def test_multiple_points_use_one_call_and_preserve_input_order() -> None:
    client = FakeQdrantClient()
    first = make_point(segment_id=uuid4(), text="First line")
    second = make_point(segment_id=uuid4(), text="Second line", offset=0.1)

    QdrantTranscriptIndexWriter(client, SCHEMA).replace_source_version(
        SOURCE_VERSION_ID, None, (first, second)
    )

    assert len(client.calls) == 1
    assert [point.id for point in client.calls[0]["points"]] == [
        str(first.chunk_id),
        str(second.chunk_id),
    ]
    assert [point.payload["text"] for point in client.calls[0]["points"]] == [
        first.payload.text,
        second.payload.text,
    ]


def test_replacement_upserts_new_points_before_deleting_retired_source() -> None:
    client = FakeQdrantClient()
    retired_source_id = uuid4()

    QdrantTranscriptIndexWriter(client, SCHEMA).replace_source_version(
        SOURCE_VERSION_ID,
        retired_source_id,
        (make_point(),),
    )

    assert len(client.calls) == 2
    assert "points" in client.calls[0]
    selector = client.calls[1]["points_selector"]
    assert isinstance(selector, models.FilterSelector)
    assert selector.filter.model_dump(exclude_none=True) == {
        "must": [
            {
                "key": "source_version_id",
                "match": {"value": str(retired_source_id)},
            }
        ]
    }
    assert client.calls[1]["wait"] is True


def test_episode_language_replacement_upserts_before_deleting_other_source_versions() -> None:
    client = FakeQdrantClient()
    new_source_id = uuid4()
    point = replace(
        make_point(),
        payload=replace(make_point().payload, source_version_id=new_source_id),
    )

    QdrantTranscriptIndexWriter(
        client,
        SCHEMA,
        replacement_mode=QdrantTranscriptReplacementMode.EPISODE_LANGUAGE,
    ).replace_source_version(new_source_id, None, (point,))

    assert len(client.calls) == 2
    assert "points" in client.calls[0]
    selector = client.calls[1]["points_selector"]
    assert isinstance(selector, models.FilterSelector)
    assert selector.filter.model_dump(exclude_none=True) == {
        "must": [
            {
                "key": "episode_id",
                "match": {"value": str(point.payload.episode_id)},
            },
            {
                "key": "language",
                "match": {"value": Language.ENGLISH.value},
            },
        ],
        "must_not": [
            {
                "key": "source_version_id",
                "match": {"value": str(new_source_id)},
            }
        ],
    }
    assert client.calls[1]["wait"] is True


def test_episode_language_replacement_upsert_failure_never_deletes_existing_points() -> None:
    class FailingClient(FakeQdrantClient):
        def upsert(self, **kwargs: Any) -> models.UpdateResult:
            self.calls.append(kwargs)
            raise RuntimeError("synthetic upsert failure")

    client = FailingClient()
    point = make_point()

    with pytest.raises(RuntimeError, match="synthetic upsert failure"):
        QdrantTranscriptIndexWriter(
            client,
            SCHEMA,
            replacement_mode=QdrantTranscriptReplacementMode.EPISODE_LANGUAGE,
        ).replace_source_version(SOURCE_VERSION_ID, None, (point,))

    assert len(client.calls) == 1
    assert "points" in client.calls[0]


def test_episode_language_replacement_rejects_mismatched_episode_before_qdrant_calls() -> None:
    client = FakeQdrantClient()
    first = make_point(segment_id=uuid4())
    second = replace(
        make_point(segment_id=uuid4()),
        payload=replace(make_point().payload, episode_id=uuid4()),
    )

    with pytest.raises(
        InvalidModelError,
        match=QdrantErrorMessages.SOURCE_VERSION_POINTS_MUST_SHARE_EPISODE_AND_LANGUAGE,
    ):
        QdrantTranscriptIndexWriter(
            client,
            SCHEMA,
            replacement_mode=QdrantTranscriptReplacementMode.EPISODE_LANGUAGE,
        ).replace_source_version(SOURCE_VERSION_ID, None, (first, second))

    assert client.calls == []


def test_episode_language_replacement_rejects_mismatched_language_before_qdrant_calls() -> None:
    client = FakeQdrantClient()
    first = make_point(segment_id=uuid4())
    second = replace(
        make_point(segment_id=uuid4()),
        payload=replace(make_point().payload, language="fr"),
    )

    with pytest.raises(
        InvalidModelError,
        match=QdrantErrorMessages.SOURCE_VERSION_POINTS_MUST_SHARE_EPISODE_AND_LANGUAGE,
    ):
        QdrantTranscriptIndexWriter(
            client,
            SCHEMA,
            replacement_mode=QdrantTranscriptReplacementMode.EPISODE_LANGUAGE,
        ).replace_source_version(SOURCE_VERSION_ID, None, (first, second))

    assert client.calls == []


def test_empty_replacement_still_deletes_retired_source() -> None:
    client = FakeQdrantClient()

    QdrantTranscriptIndexWriter(client, SCHEMA).replace_source_version(
        SOURCE_VERSION_ID,
        uuid4(),
        (),
    )

    assert len(client.calls) == 1
    assert "points_selector" in client.calls[0]


def test_upsert_failure_never_retires_previous_source() -> None:
    class FailingClient(FakeQdrantClient):
        def upsert(self, **kwargs: Any) -> models.UpdateResult:
            self.calls.append(kwargs)
            raise RuntimeError("synthetic upsert failure")

    client = FailingClient()

    with pytest.raises(RuntimeError, match="synthetic upsert failure"):
        QdrantTranscriptIndexWriter(client, SCHEMA).replace_source_version(
            SOURCE_VERSION_ID,
            uuid4(),
            (make_point(),),
        )

    assert len(client.calls) == 1
    assert "points" in client.calls[0]


@pytest.mark.parametrize(
    ("points", "message"),
    [
        (
            (make_point(), make_point()),
            QdrantErrorMessages.SOURCE_VERSION_POINTS_MUST_BE_UNIQUE,
        ),
        (
            (
                replace(
                    make_point(), payload=replace(make_point().payload, source_version_id=uuid4())
                ),
            ),
            QdrantErrorMessages.SOURCE_VERSION_POINT_MUST_MATCH_NEW,
        ),
        (
            (
                replace(
                    make_point(),
                    payload=replace(make_point().payload, index_revision="obsolete"),
                ),
            ),
            QdrantErrorMessages.SOURCE_VERSION_POINT_REVISION_MUST_MATCH,
        ),
        (
            (
                replace(
                    make_point(),
                    payload=replace(make_point().payload, member_segment_ids=()),
                ),
            ),
            QdrantErrorMessages.SOURCE_VERSION_POINT_MEMBERS_MUST_BE_VALID,
        ),
    ],
)
def test_invalid_replacement_rejects_before_qdrant_calls(
    points: tuple[TranscriptIndexPoint, ...],
    message: str,
) -> None:
    client = FakeQdrantClient()

    with pytest.raises(InvalidModelError, match=message):
        QdrantTranscriptIndexWriter(client, SCHEMA).replace_source_version(
            SOURCE_VERSION_ID,
            None,
            points,
        )

    assert client.calls == []


def test_dense_dimension_mismatch_rejects_before_qdrant_calls() -> None:
    client = FakeQdrantClient()
    point = replace(
        make_point(),
        vector=DocumentVector(
            HybridVector(
                DenseVector((0.1, 0.2)),
                SparseVector((1,), (1.0,)),
            )
        ),
    )

    with pytest.raises(
        InvalidModelError,
        match=QdrantErrorMessages.SOURCE_VERSION_POINT_DENSE_DIMENSION_MUST_MATCH,
    ):
        QdrantTranscriptIndexWriter(client, SCHEMA).replace_source_version(
            SOURCE_VERSION_ID,
            None,
            (point,),
        )

    assert client.calls == []


def test_new_and_retired_source_ids_must_differ() -> None:
    client = FakeQdrantClient()

    with pytest.raises(
        InvalidModelError,
        match=QdrantErrorMessages.SOURCE_VERSION_REPLACEMENT_IDS_MUST_DIFFER,
    ):
        QdrantTranscriptIndexWriter(client, SCHEMA).replace_source_version(
            SOURCE_VERSION_ID,
            SOURCE_VERSION_ID,
            (),
        )

    assert client.calls == []
