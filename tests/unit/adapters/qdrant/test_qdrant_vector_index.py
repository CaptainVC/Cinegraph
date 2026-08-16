from typing import Any
from uuid import UUID

import pytest
from qdrant_client.http import models

from cinegraph.common.error_messages import RetrievalErrorMessages
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.retrieval.retrieval_scope import (
    EpisodeVisibilityScope,
    RetrievalScope,
)
from cinegraph.domain.retrieval.vector_data import (
    DenseVector,
    HybridVector,
    QueryVector,
    SparseVector,
)
from cinegraph.adapters.qdrant.qdrant_vector_index import QdrantVectorIndex
from tests.factories import make_episode_ref


SERIES_ID = UUID("00000000-0000-0000-0000-000000000011")
SEASON_ID = UUID("00000000-0000-0000-0000-000000000101")
EPISODE_ID = UUID("00000000-0000-0000-0000-000000001001")
SEGMENT_ID = UUID("00000000-0000-0000-0000-000000002001")


class FakeQdrantClient:
    def __init__(self, points: list[models.ScoredPoint]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response = models.QueryResponse(points=points)

    def query_points(self, **kwargs: Any) -> models.QueryResponse:
        self.calls.append(kwargs)
        return self._response


def make_query() -> QueryVector:
    return QueryVector(
        vector=HybridVector(
            dense=DenseVector(values=(0.1, 0.2, 0.3)),
            sparse=SparseVector(indices=(2, 7), values=(0.4, 0.8)),
        )
    )


def make_scope(*, safe_until_ms: int | None = None) -> RetrievalScope:
    episode = make_episode_ref(
        series_id=SERIES_ID,
        season_id=SEASON_ID,
        episode_id=EPISODE_ID,
        season_number=2,
        episode_number=3,
    )
    return RetrievalScope(
        series_id=SERIES_ID,
        episode_scopes=(EpisodeVisibilityScope(episode, safe_until_ms),),
    )


def make_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "series_id": str(SERIES_ID),
        "season_id": str(SEASON_ID),
        "episode_id": str(EPISODE_ID),
        "season_number": 2,
        "episode_number": 3,
        "start_ms": 1_000,
        "end_ms": 2_000,
        "text": "Claire says hello.",
    }
    payload.update(overrides)
    return payload


def make_point(
    *,
    segment_id: UUID = SEGMENT_ID,
    score: float = 0.75,
    payload: dict[str, Any] | None = None,
) -> models.ScoredPoint:
    return models.ScoredPoint(
        id=segment_id,
        version=1,
        score=score,
        payload=payload if payload is not None else make_payload(),
    )


def test_empty_scope_returns_empty_without_calling_qdrant() -> None:
    client = FakeQdrantClient(points=[])
    index = QdrantVectorIndex(client, "transcript_segments")
    scope = RetrievalScope(series_id=SERIES_ID, episode_scopes=())

    assert index.search_hybrid(make_query(), scope, 3) == ()
    assert client.calls == []


def test_invalid_limit_raises_central_error_without_calling_qdrant() -> None:
    client = FakeQdrantClient(points=[])
    index = QdrantVectorIndex(client, "transcript_segments")

    with pytest.raises(
        ValueError,
        match=RetrievalErrorMessages.SEARCH_LIMIT_MUST_BE_POSITIVE,
    ):
        index.search_hybrid(make_query(), make_scope(), 0)

    assert client.calls == []


def test_normal_query_builds_dense_sparse_rrf_request_and_shared_filter() -> None:
    client = FakeQdrantClient(points=[make_point()])
    index = QdrantVectorIndex(client, "transcript_segments")

    index.search_hybrid(make_query(), make_scope(safe_until_ms=2_000), 4)

    assert len(client.calls) == 1
    call = client.calls[0]
    dense, sparse = call["prefetch"]
    assert dense.using == "dense"
    assert dense.query == [0.1, 0.2, 0.3]
    assert dense.limit == 4
    assert sparse.using == "sparse"
    assert sparse.query == models.SparseVector(indices=[2, 7], values=[0.4, 0.8])
    assert sparse.limit == 4
    assert dense.filter is sparse.filter
    serialized = dense.filter.model_dump(exclude_none=True)
    assert serialized["must"][:3] == [
        {"key": "series_id", "match": {"value": str(SERIES_ID)}},
        {"key": "source_status", "match": {"value": "active"}},
        {
            "key": "review_status",
                "match": {
                    "any": [
                        "automated_reviewed",
                        "hybrid_reviewed",
                        "reviewed",
                    ]
                },
        },
    ]
    assert serialized["must"][3]["should"][0]["must"][1] == {
        "key": "end_ms",
        "range": {"lte": 2_000},
    }
    assert call["query"] == models.FusionQuery(fusion=models.Fusion.RRF)
    assert call["collection_name"] == "transcript_segments"
    assert call["limit"] == 4
    assert call["with_payload"] is True
    assert call["with_vectors"] is False


def test_mapped_point_retains_score_episode_and_transcript_fields() -> None:
    client = FakeQdrantClient(points=[make_point()])
    result = QdrantVectorIndex(client, "transcript_segments").search_hybrid(
        make_query(), make_scope(), 1
    )

    assert len(result) == 1
    mapped = result[0]
    assert mapped.segment_id == SEGMENT_ID
    assert mapped.episode.series_id == SERIES_ID
    assert mapped.episode.season_id == SEASON_ID
    assert mapped.episode.episode_id == EPISODE_ID
    assert mapped.episode.position.season_number == 2
    assert mapped.episode.position.episode_number == 3
    assert mapped.start_ms == 1_000
    assert mapped.end_ms == 2_000
    assert mapped.text == "Claire says hello."
    assert mapped.score == 0.75


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {key: value for key, value in make_payload().items() if key != "text"},
            RetrievalErrorMessages.QDRANT_RESULT_PAYLOAD_MUST_BE_COMPLETE,
        ),
        (
            make_payload(series_id=str(UUID("00000000-0000-0000-0000-000000000099"))),
            RetrievalErrorMessages.QDRANT_RESULT_SERIES_MUST_MATCH_SCOPE,
        ),
    ],
)
def test_malformed_payload_raises_invalid_model_error(
    payload: dict[str, Any], message: str
) -> None:
    client = FakeQdrantClient(points=[make_point(payload=payload)])

    with pytest.raises(InvalidModelError, match=message):
        QdrantVectorIndex(client, "transcript_segments").search_hybrid(
            make_query(), make_scope(), 1
        )


def test_malformed_point_id_raises_invalid_model_error() -> None:
    point = models.ScoredPoint(
        id="not-a-uuid",
        version=1,
        score=0.75,
        payload=make_payload(),
    )
    client = FakeQdrantClient(points=[point])

    with pytest.raises(
        InvalidModelError,
        match=RetrievalErrorMessages.QDRANT_RESULT_IDS_MUST_BE_VALID,
    ):
        QdrantVectorIndex(client, "transcript_segments").search_hybrid(
            make_query(), make_scope(), 1
        )


def test_multiple_points_preserve_qdrant_response_order() -> None:
    first = make_point(segment_id=UUID(int=21), score=0.2)
    second = make_point(segment_id=UUID(int=22), score=0.9)
    client = FakeQdrantClient(points=[first, second])

    result = QdrantVectorIndex(client, "transcript_segments").search_hybrid(
        make_query(), make_scope(), 2
    )

    assert [segment.segment_id for segment in result] == [UUID(int=21), UUID(int=22)]
    assert [segment.score for segment in result] == [0.2, 0.9]
