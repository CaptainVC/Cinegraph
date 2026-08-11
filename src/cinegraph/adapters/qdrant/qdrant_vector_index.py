import math
from collections.abc import Mapping
from numbers import Real
from typing import Any, Protocol
from uuid import UUID

from qdrant_client.http import models

from cinegraph.adapters.qdrant.retrieval_scope_filter import (
    compile_retrieval_scope_filter,
)
from cinegraph.common.error_messages import RetrievalErrorMessages
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.watch_state.episode_watch_state import (
    EpisodePosition,
    EpisodeRef,
)
from cinegraph.domain.retrieval.retrieval_scope import RetrievalScope
from cinegraph.domain.retrieval.vector_data import QueryVector
from cinegraph.ports.retrieval.vector_index import RetrievedSegment, VectorIndex


class QdrantQueryClient(Protocol):
    # Define the narrow Qdrant query surface required by the synchronous index.
    def query_points(
        self,
        *,
        collection_name: str,
        prefetch: list[models.Prefetch],
        query: models.FusionQuery,
        limit: int,
        with_payload: bool,
        with_vectors: bool,
    ) -> Any: ...


class QdrantVectorIndex(VectorIndex):
    # Store the client and collection used for hybrid evidence searches.
    def __init__(self, client: QdrantQueryClient, collection_name: str) -> None:
        self._client = client
        self._collection_name = collection_name

    # Search Qdrant with dense and sparse prefetches under one visibility filter.
    def search_hybrid(
        self,
        query: QueryVector,
        scope: RetrievalScope,
        limit: int,
    ) -> tuple[RetrievedSegment, ...]:
        if limit < 1:
            raise ValueError(RetrievalErrorMessages.SEARCH_LIMIT_MUST_BE_POSITIVE)
        if not scope.episode_scopes:
            return ()

        # Compile visibility once so both prefetch branches share the same object.
        compiled_filter = compile_retrieval_scope_filter(scope)
        dense_prefetch = models.Prefetch(
            query=list(query.vector.dense.values),
            using="dense",
            filter=compiled_filter,
            limit=limit,
        )
        sparse_prefetch = models.Prefetch(
            query=models.SparseVector(
                indices=list(query.vector.sparse.indices),
                values=list(query.vector.sparse.values),
            ),
            using="sparse",
            filter=compiled_filter,
            limit=limit,
        )

        # Ask Qdrant for reciprocal-rank fusion and preserve its returned order.
        response = self._client.query_points(
            collection_name=self._collection_name,
            prefetch=[dense_prefetch, sparse_prefetch],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return tuple(self._map_point(point, scope) for point in response.points)

    # Convert one validated Qdrant point into the retrieval port model.
    def _map_point(self, point: Any, scope: RetrievalScope) -> RetrievedSegment:
        payload = point.payload
        required_keys = {
            "series_id",
            "season_id",
            "episode_id",
            "season_number",
            "episode_number",
            "start_ms",
            "end_ms",
            "text",
        }
        if not isinstance(payload, Mapping) or not required_keys.issubset(payload):
            raise InvalidModelError(RetrievalErrorMessages.QDRANT_RESULT_PAYLOAD_MUST_BE_COMPLETE)

        # Parse identifiers before constructing the immutable episode reference.
        try:
            segment_id = UUID(str(point.id))
            series_id = UUID(payload["series_id"])
            season_id = UUID(payload["season_id"])
            episode_id = UUID(payload["episode_id"])
        except (AttributeError, TypeError, ValueError):
            raise InvalidModelError(RetrievalErrorMessages.QDRANT_RESULT_IDS_MUST_BE_VALID)
        if series_id != scope.series_id:
            raise InvalidModelError(
                RetrievalErrorMessages.QDRANT_RESULT_SERIES_MUST_MATCH_SCOPE
            )

        # Enforce backend scalar types before applying domain timing invariants.
        numeric_fields = (
            payload["season_number"],
            payload["episode_number"],
            payload["start_ms"],
            payload["end_ms"],
        )
        if (
            any(isinstance(value, bool) or not isinstance(value, int) for value in numeric_fields)
            or payload["season_number"] < 1
            or payload["episode_number"] < 1
        ):
            raise InvalidModelError(
                RetrievalErrorMessages.QDRANT_RESULT_NUMERIC_FIELDS_MUST_BE_VALID
            )
        text = payload["text"]
        if not isinstance(text, str) or not text or text.strip() != text:
            raise InvalidModelError(RetrievalErrorMessages.QDRANT_RESULT_TEXT_MUST_BE_VALID)
        score = point.score
        if isinstance(score, bool) or not isinstance(score, Real) or not math.isfinite(score):
            raise InvalidModelError(RetrievalErrorMessages.QDRANT_RESULT_SCORE_MUST_BE_FINITE)
        if payload["start_ms"] < 0 or payload["end_ms"] <= payload["start_ms"]:
            raise InvalidModelError(
                RetrievalErrorMessages.QDRANT_RESULT_NUMERIC_FIELDS_MUST_BE_VALID
            )

        # Build the episode metadata and segment after all backend values are safe.
        episode = EpisodeRef(
            series_id=series_id,
            season_id=season_id,
            episode_id=episode_id,
            position=EpisodePosition(
                season_number=payload["season_number"],
                episode_number=payload["episode_number"],
            ),
        )
        return RetrievedSegment(
            segment_id=segment_id,
            episode=episode,
            start_ms=payload["start_ms"],
            end_ms=payload["end_ms"],
            text=text,
            score=float(score),
        )