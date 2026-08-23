from typing import Any
from uuid import UUID

import pytest
from qdrant_client.http import models
from tests.factories import make_episode_ref

from cinegraph.adapters.qdrant.qdrant_vector_index import QdrantVectorIndex
from cinegraph.common.error_messages import RetrievalErrorMessages
from cinegraph.config import (
    DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA,
    HybridRetrievalConfiguration,
)
from cinegraph.config.transcript_chunking import TRANSCRIPT_INDEX_REVISION
from cinegraph.domain.enums.enum import (
    Language,
    RightsStatus,
    SourceReviewStatus,
    SourceVersionStatus,
)
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

SERIES_ID = UUID("00000000-0000-0000-0000-000000000011")
SEASON_ID = UUID("00000000-0000-0000-0000-000000000101")
EPISODE_ID = UUID("00000000-0000-0000-0000-000000001001")
SEGMENT_ID = UUID("00000000-0000-0000-0000-000000002001")
SOURCE_VERSION_ID = UUID("00000000-0000-0000-0000-000000000701")
SCHEMA = DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA


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
        "source_version_id": str(SOURCE_VERSION_ID),
        "series_id": str(SERIES_ID),
        "season_id": str(SEASON_ID),
        "episode_id": str(EPISODE_ID),
        "season_number": 2,
        "episode_number": 3,
        "start_ms": 1_000,
        "end_ms": 2_000,
        "text": "Claire says hello.",
        "language": Language.ENGLISH.value,
        "rights_status": RightsStatus.ALLOWED.value,
        "source_status": SourceVersionStatus.ACTIVE.value,
        "review_status": SourceReviewStatus.REVIEWED.value,
        "index_revision": TRANSCRIPT_INDEX_REVISION,
        "member_segment_ids": [str(SEGMENT_ID)],
        "chunk_ordinal": 0,
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
    index = QdrantVectorIndex(client, SCHEMA)
    scope = RetrievalScope(series_id=SERIES_ID, episode_scopes=())

    assert index.search_hybrid(make_query(), scope, 3) == ()
    assert client.calls == []


def test_invalid_limit_raises_central_error_without_calling_qdrant() -> None:
    client = FakeQdrantClient(points=[])
    index = QdrantVectorIndex(client, SCHEMA)

    with pytest.raises(
        ValueError,
        match=RetrievalErrorMessages.SEARCH_LIMIT_MUST_BE_POSITIVE,
    ):
        index.search_hybrid(make_query(), make_scope(), 0)

    assert client.calls == []


@pytest.mark.parametrize("limit", [True, 1.0, "1", None])
def test_non_integer_limit_rejects_before_qdrant_call(limit: object) -> None:
    client = FakeQdrantClient(points=[])

    with pytest.raises(
        ValueError,
        match=RetrievalErrorMessages.SEARCH_LIMIT_MUST_BE_POSITIVE,
    ):
        QdrantVectorIndex(client, SCHEMA).search_hybrid(  # type: ignore[arg-type]
            make_query(),
            make_scope(),
            limit,
        )

    assert client.calls == []


def test_limit_above_configured_maximum_rejects_before_qdrant_call() -> None:
    client = FakeQdrantClient(points=[])
    configuration = HybridRetrievalConfiguration(
        candidate_overfetch_cap=5,
        max_requested_result_limit=5,
    )

    with pytest.raises(
        ValueError,
        match=RetrievalErrorMessages.SEARCH_LIMIT_EXCEEDS_CONFIGURED_MAXIMUM,
    ):
        QdrantVectorIndex(client, SCHEMA, configuration).search_hybrid(
            make_query(),
            make_scope(),
            6,
        )

    assert client.calls == []


def test_normal_query_builds_dense_sparse_rrf_request_and_shared_filter() -> None:
    client = FakeQdrantClient(points=[make_point()])
    index = QdrantVectorIndex(client, SCHEMA)

    index.search_hybrid(make_query(), make_scope(safe_until_ms=2_000), 4)

    assert len(client.calls) == 1
    call = client.calls[0]
    dense, sparse = call["prefetch"]
    assert dense.using == "dense"
    assert dense.query == [0.1, 0.2, 0.3]
    assert dense.limit == 12
    assert sparse.using == "sparse"
    assert sparse.query == models.SparseVector(indices=[2, 7], values=[0.4, 0.8])
    assert sparse.limit == 12
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
    visibility = next(item for item in serialized["must"] if "should" in item)
    assert visibility["should"][0]["must"][1] == {
        "key": "end_ms",
        "range": {"lte": 2_000},
    }
    assert call["query"] == models.FusionQuery(fusion=models.Fusion.RRF)
    assert call["collection_name"] == "transcript_segments"
    assert call["limit"] == 12
    assert call["with_payload"] is True
    assert call["with_vectors"] is False


def test_mapped_point_retains_score_episode_and_transcript_fields() -> None:
    client = FakeQdrantClient(points=[make_point()])
    result = QdrantVectorIndex(client, SCHEMA).search_hybrid(make_query(), make_scope(), 1)

    assert len(result) == 1
    mapped = result[0]
    assert mapped.segment_id == SEGMENT_ID
    assert mapped.source_version_id == SOURCE_VERSION_ID
    assert mapped.episode.series_id == SERIES_ID
    assert mapped.episode.season_id == SEASON_ID
    assert mapped.episode.episode_id == EPISODE_ID
    assert mapped.episode.position.season_number == 2
    assert mapped.episode.position.episode_number == 3
    assert mapped.start_ms == 1_000
    assert mapped.end_ms == 2_000
    assert mapped.text == "Claire says hello."
    assert mapped.language is Language.ENGLISH
    assert mapped.rights_status is RightsStatus.ALLOWED
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
        (
            make_payload(language="not-a-language"),
            RetrievalErrorMessages.QDRANT_RESULT_GOVERNANCE_FIELDS_MUST_BE_VALID,
        ),
        (
            make_payload(rights_status=RightsStatus.RESTRICTED.value),
            RetrievalErrorMessages.QDRANT_RESULT_GOVERNANCE_FIELDS_MUST_BE_VALID,
        ),
        (
            make_payload(source_status=SourceVersionStatus.RETIRED.value),
            RetrievalErrorMessages.QDRANT_RESULT_SOURCE_STATUS_MUST_BE_ACTIVE,
        ),
        (
            make_payload(review_status=SourceReviewStatus.PENDING.value),
            RetrievalErrorMessages.QDRANT_RESULT_REVIEW_STATUS_MUST_BE_APPROVED,
        ),
        (
            make_payload(index_revision="obsolete"),
            RetrievalErrorMessages.QDRANT_RESULT_INDEX_REVISION_MUST_MATCH,
        ),
        (
            make_payload(member_segment_ids="not-a-sequence"),
            RetrievalErrorMessages.QDRANT_RESULT_MEMBER_SEGMENTS_MUST_BE_VALID,
        ),
        (
            make_payload(member_segment_ids=[]),
            RetrievalErrorMessages.QDRANT_RESULT_MEMBER_SEGMENTS_MUST_BE_VALID,
        ),
    ],
)
def test_malformed_payload_raises_invalid_model_error(
    payload: dict[str, Any], message: str
) -> None:
    client = FakeQdrantClient(points=[make_point(payload=payload)])

    with pytest.raises(InvalidModelError, match=message):
        QdrantVectorIndex(client, SCHEMA).search_hybrid(make_query(), make_scope(), 1)


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
        QdrantVectorIndex(client, SCHEMA).search_hybrid(make_query(), make_scope(), 1)


@pytest.mark.parametrize(
    "payload",
    [
        make_payload(episode_id=str(UUID(int=999))),
        make_payload(season_number=3),
        make_payload(end_ms=2_001),
    ],
)
def test_backend_result_outside_visibility_scope_is_rejected(
    payload: dict[str, Any],
) -> None:
    client = FakeQdrantClient(points=[make_point(payload=payload)])

    with pytest.raises(
        InvalidModelError,
        match=RetrievalErrorMessages.QDRANT_RESULT_MUST_MATCH_VISIBILITY_SCOPE,
    ):
        QdrantVectorIndex(client, SCHEMA).search_hybrid(
            make_query(), make_scope(safe_until_ms=2_000), 1
        )


def test_multiple_points_preserve_qdrant_response_order() -> None:
    first = make_point(segment_id=UUID(int=21), score=0.2)
    second = make_point(
        segment_id=UUID(int=22),
        score=0.9,
        payload=make_payload(member_segment_ids=[str(UUID(int=23))]),
    )
    client = FakeQdrantClient(points=[first, second])

    result = QdrantVectorIndex(client, SCHEMA).search_hybrid(make_query(), make_scope(), 2)

    assert [segment.segment_id for segment in result] == [UUID(int=21), UUID(int=22)]
    assert [segment.score for segment in result] == [0.2, 0.9]


def test_redundant_member_overlap_is_suppressed_after_overfetch() -> None:
    shared_member_id = UUID(int=31)
    first = make_point(
        segment_id=UUID(int=32),
        payload=make_payload(member_segment_ids=[str(shared_member_id)]),
    )
    redundant = make_point(
        segment_id=UUID(int=33),
        payload=make_payload(member_segment_ids=[str(shared_member_id)]),
    )
    client = FakeQdrantClient(points=[first, redundant])

    result = QdrantVectorIndex(client, SCHEMA).search_hybrid(
        make_query(),
        make_scope(),
        2,
    )

    assert tuple(item.segment_id for item in result) == (UUID(int=32),)
