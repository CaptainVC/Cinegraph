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
from cinegraph.config.qdrant import (
    DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA,
    QDRANT_END_MS_FIELD,
    QDRANT_EPISODE_ID_FIELD,
    QDRANT_EPISODE_NUMBER_FIELD,
    QDRANT_LANGUAGE_FIELD,
    QDRANT_RIGHTS_STATUS_FIELD,
    QDRANT_SEASON_ID_FIELD,
    QDRANT_SEASON_NUMBER_FIELD,
    QDRANT_SERIES_ID_FIELD,
    QDRANT_SOURCE_VERSION_ID_FIELD,
    QDRANT_START_MS_FIELD,
    QDRANT_TEXT_FIELD,
    QDRANT_TRANSCRIPT_REQUIRED_PAYLOAD_FIELDS,
)
from cinegraph.domain.enums.enum import Language, RightsStatus
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
            using=DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA.dense_vector_name,
            filter=compiled_filter,
            limit=limit,
        )
        sparse_prefetch = models.Prefetch(
            query=models.SparseVector(
                indices=list(query.vector.sparse.indices),
                values=list(query.vector.sparse.values),
            ),
            using=DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA.sparse_vector_name,
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
        if not isinstance(payload, Mapping) or not QDRANT_TRANSCRIPT_REQUIRED_PAYLOAD_FIELDS.issubset(payload):
            raise InvalidModelError(RetrievalErrorMessages.QDRANT_RESULT_PAYLOAD_MUST_BE_COMPLETE)

        # Parse identifiers before constructing the immutable episode reference.
        try:
            segment_id = UUID(str(point.id))
            source_version_id = UUID(payload[QDRANT_SOURCE_VERSION_ID_FIELD])
            series_id = UUID(payload[QDRANT_SERIES_ID_FIELD])
            season_id = UUID(payload[QDRANT_SEASON_ID_FIELD])
            episode_id = UUID(payload[QDRANT_EPISODE_ID_FIELD])
        except (AttributeError, TypeError, ValueError):
            raise InvalidModelError(RetrievalErrorMessages.QDRANT_RESULT_IDS_MUST_BE_VALID)
        if series_id != scope.series_id:
            raise InvalidModelError(
                RetrievalErrorMessages.QDRANT_RESULT_SERIES_MUST_MATCH_SCOPE
            )

        try:
            language = Language(payload[QDRANT_LANGUAGE_FIELD])
            rights_status = RightsStatus(payload[QDRANT_RIGHTS_STATUS_FIELD])
        except (TypeError, ValueError):
            raise InvalidModelError(
                RetrievalErrorMessages.QDRANT_RESULT_GOVERNANCE_FIELDS_MUST_BE_VALID
            )

        # Enforce backend scalar types before applying domain timing invariants.
        numeric_fields = (
            payload[QDRANT_SEASON_NUMBER_FIELD],
            payload[QDRANT_EPISODE_NUMBER_FIELD],
            payload[QDRANT_START_MS_FIELD],
            payload[QDRANT_END_MS_FIELD],
        )
        if (
            any(isinstance(value, bool) or not isinstance(value, int) for value in numeric_fields)
            or payload[QDRANT_SEASON_NUMBER_FIELD] < 1
            or payload[QDRANT_EPISODE_NUMBER_FIELD] < 1
        ):
            raise InvalidModelError(
                RetrievalErrorMessages.QDRANT_RESULT_NUMERIC_FIELDS_MUST_BE_VALID
            )
        text = payload[QDRANT_TEXT_FIELD]
        if not isinstance(text, str) or not text or text.strip() != text:
            raise InvalidModelError(RetrievalErrorMessages.QDRANT_RESULT_TEXT_MUST_BE_VALID)
        score = point.score
        if isinstance(score, bool) or not isinstance(score, Real) or not math.isfinite(score):
            raise InvalidModelError(RetrievalErrorMessages.QDRANT_RESULT_SCORE_MUST_BE_FINITE)
        if (
            payload[QDRANT_START_MS_FIELD] < 0
            or payload[QDRANT_END_MS_FIELD] <= payload[QDRANT_START_MS_FIELD]
        ):
            raise InvalidModelError(
                RetrievalErrorMessages.QDRANT_RESULT_NUMERIC_FIELDS_MUST_BE_VALID
            )

        # Build the episode metadata and segment after all backend values are safe.
        episode = EpisodeRef(
            series_id=series_id,
            season_id=season_id,
            episode_id=episode_id,
            position=EpisodePosition(
                season_number=payload[QDRANT_SEASON_NUMBER_FIELD],
                episode_number=payload[QDRANT_EPISODE_NUMBER_FIELD],
            ),
        )
        visibility_scope = next(
            (
                item
                for item in scope.episode_scopes
                if item.episode.episode_id == episode.episode_id
            ),
            None,
        )
        if (
            visibility_scope is None
            or visibility_scope.episode != episode
            or (
                visibility_scope.safe_until_ms is not None
                and payload[QDRANT_END_MS_FIELD] > visibility_scope.safe_until_ms
            )
        ):
            raise InvalidModelError(
                RetrievalErrorMessages.QDRANT_RESULT_MUST_MATCH_VISIBILITY_SCOPE
            )
        return RetrievedSegment(
            segment_id=segment_id,
            source_version_id=source_version_id,
            episode=episode,
            start_ms=payload[QDRANT_START_MS_FIELD],
            end_ms=payload[QDRANT_END_MS_FIELD],
            text=text,
            language=language,
            rights_status=rights_status,
            score=float(score),
        )
