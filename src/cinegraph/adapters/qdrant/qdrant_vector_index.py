import math
from collections.abc import Mapping
from numbers import Real
from typing import Any, Protocol
from uuid import UUID

from qdrant_client.http import models

from cinegraph.adapters.qdrant.retrieval_scope_filter import compile_retrieval_scope_filter
from cinegraph.common.error_messages import RetrievalErrorMessages
from cinegraph.config import (
    DEFAULT_HYBRID_RETRIEVAL_CONFIGURATION,
    HybridRetrievalConfiguration,
)
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
    QDRANT_TRANSCRIPT_REQUIRED_PAYLOAD_FIELDS,
    QdrantTranscriptCollectionSchema,
)
from cinegraph.config.transcript_chunking import TRANSCRIPT_INDEX_REVISION
from cinegraph.domain.enums.enum import (
    Language,
    RightsStatus,
    SourceReviewStatus,
    SourceVersionStatus,
)
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.source.review_status import is_source_version_approved
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodePosition, EpisodeRef
from cinegraph.domain.retrieval.retrieval_scope import RetrievalScope
from cinegraph.domain.retrieval.vector_data import QueryVector
from cinegraph.ports.retrieval.vector_index import RetrievedSegment, VectorIndex


class QdrantQueryClient(Protocol):
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

    def retrieve(
        self,
        *,
        collection_name: str,
        ids: list[str],
        with_payload: bool,
        with_vectors: bool,
    ) -> Any: ...


class QdrantVectorIndex(VectorIndex):
    def __init__(
        self,
        client: QdrantQueryClient,
        schema: QdrantTranscriptCollectionSchema,
        configuration: HybridRetrievalConfiguration = DEFAULT_HYBRID_RETRIEVAL_CONFIGURATION,
    ) -> None:
        self._client = client
        self._schema = schema
        self._configuration = configuration

    def search_hybrid(
        self, query: QueryVector, scope: RetrievalScope, limit: int
    ) -> tuple[RetrievedSegment, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError(RetrievalErrorMessages.SEARCH_LIMIT_MUST_BE_POSITIVE)
        if limit > self._configuration.max_requested_result_limit:
            raise ValueError(RetrievalErrorMessages.SEARCH_LIMIT_EXCEEDS_CONFIGURED_MAXIMUM)
        if not scope.episode_scopes:
            return ()
        candidate_limit = min(
            self._configuration.candidate_overfetch_cap,
            max(limit, limit * self._configuration.candidate_overfetch_multiplier),
        )
        compiled_filter = compile_retrieval_scope_filter(scope)
        prefetch = [
            models.Prefetch(
                query=list(query.vector.dense.values),
                using=self._schema.dense_vector_name,
                filter=compiled_filter,
                limit=candidate_limit,
            ),
            models.Prefetch(
                query=models.SparseVector(
                    indices=list(query.vector.sparse.indices),
                    values=list(query.vector.sparse.values),
                ),
                using=self._schema.sparse_vector_name,
                filter=compiled_filter,
                limit=candidate_limit,
            ),
        ]
        response = self._client.query_points(
            collection_name=self._schema.collection_name,
            prefetch=prefetch,
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=candidate_limit,
            with_payload=True,
            with_vectors=False,
        )
        selected: list[RetrievedSegment] = []
        for candidate in (self._map_point(point, scope) for point in response.points):
            if any(
                candidate.source_version_id == item.source_version_id
                and candidate.episode.episode_id == item.episode.episode_id
                and self._overlap(candidate, item)
                > self._configuration.maximum_member_overlap_ratio
                for item in selected
            ):
                continue
            selected.append(candidate)
            if len(selected) >= limit:
                break
        return tuple(selected)

    def retrieve_by_ids(
        self, segment_ids: tuple[UUID, ...], scope: RetrievalScope
    ) -> tuple[RetrievedSegment, ...]:
        if not segment_ids or len(segment_ids) != len(set(segment_ids)):
            raise ValueError(RetrievalErrorMessages.SEARCH_LIMIT_MUST_BE_POSITIVE)
        records = self._client.retrieve(
            collection_name=self._schema.collection_name,
            ids=[str(item) for item in segment_ids],
            with_payload=True,
            with_vectors=False,
        )
        by_id = {
            UUID(str(item.id)): item
            for item in records
            if getattr(item, "id", None) is not None
        }
        return tuple(
            self._map_point(by_id[item], scope, missing_score=0.0)
            for item in segment_ids
            if item in by_id
        )

    @staticmethod
    def _overlap(left: RetrievedSegment, right: RetrievedSegment) -> float:
        first, second = set(left.member_segment_ids), set(right.member_segment_ids)
        return len(first & second) / len(first | second) if first and second else 0.0

    def _map_point(
        self,
        point: Any,
        scope: RetrievalScope,
        *,
        missing_score: float | None = None,
    ) -> RetrievedSegment:
        payload = point.payload
        if not isinstance(
            payload, Mapping
        ) or not QDRANT_TRANSCRIPT_REQUIRED_PAYLOAD_FIELDS.issubset(payload):
            raise InvalidModelError(RetrievalErrorMessages.QDRANT_RESULT_PAYLOAD_MUST_BE_COMPLETE)
        try:
            chunk_id = UUID(str(point.id))
            source_version_id = UUID(str(payload[QDRANT_SOURCE_VERSION_ID_FIELD]))
            series_id = UUID(str(payload[QDRANT_SERIES_ID_FIELD]))
            season_id = UUID(str(payload[QDRANT_SEASON_ID_FIELD]))
            episode_id = UUID(str(payload[QDRANT_EPISODE_ID_FIELD]))
        except (AttributeError, TypeError, ValueError):
            raise InvalidModelError(RetrievalErrorMessages.QDRANT_RESULT_IDS_MUST_BE_VALID)
        raw_member_ids = payload[QDRANT_MEMBER_SEGMENT_IDS_FIELD]
        if not isinstance(raw_member_ids, (list, tuple)):
            raise InvalidModelError(
                RetrievalErrorMessages.QDRANT_RESULT_MEMBER_SEGMENTS_MUST_BE_VALID
            )
        try:
            member_ids = tuple(UUID(str(item)) for item in raw_member_ids)
        except (AttributeError, TypeError, ValueError):
            raise InvalidModelError(
                RetrievalErrorMessages.QDRANT_RESULT_MEMBER_SEGMENTS_MUST_BE_VALID
            )
        if not member_ids or len(set(member_ids)) != len(member_ids):
            raise InvalidModelError(
                RetrievalErrorMessages.QDRANT_RESULT_MEMBER_SEGMENTS_MUST_BE_VALID
            )
        if payload[QDRANT_SOURCE_STATUS_FIELD] != SourceVersionStatus.ACTIVE.value:
            raise InvalidModelError(
                RetrievalErrorMessages.QDRANT_RESULT_SOURCE_STATUS_MUST_BE_ACTIVE
            )
        if payload[QDRANT_REVIEW_STATUS_FIELD] not in {
            item.value for item in SourceReviewStatus if is_source_version_approved(item)
        }:
            raise InvalidModelError(
                RetrievalErrorMessages.QDRANT_RESULT_REVIEW_STATUS_MUST_BE_APPROVED
            )
        if payload[QDRANT_RIGHTS_STATUS_FIELD] != RightsStatus.ALLOWED.value:
            raise InvalidModelError(
                RetrievalErrorMessages.QDRANT_RESULT_GOVERNANCE_FIELDS_MUST_BE_VALID
            )
        if payload[QDRANT_INDEX_REVISION_FIELD] != TRANSCRIPT_INDEX_REVISION:
            raise InvalidModelError(RetrievalErrorMessages.QDRANT_RESULT_INDEX_REVISION_MUST_MATCH)
        if series_id != scope.series_id:
            raise InvalidModelError(RetrievalErrorMessages.QDRANT_RESULT_SERIES_MUST_MATCH_SCOPE)
        try:
            language = Language(payload[QDRANT_LANGUAGE_FIELD])
            rights_status = RightsStatus(payload[QDRANT_RIGHTS_STATUS_FIELD])
        except (TypeError, ValueError):
            raise InvalidModelError(
                RetrievalErrorMessages.QDRANT_RESULT_GOVERNANCE_FIELDS_MUST_BE_VALID
            )
        numbers = tuple(
            payload[field]
            for field in (
                QDRANT_SEASON_NUMBER_FIELD,
                QDRANT_EPISODE_NUMBER_FIELD,
                QDRANT_START_MS_FIELD,
                QDRANT_END_MS_FIELD,
                QDRANT_CHUNK_ORDINAL_FIELD,
            )
        )
        if (
            any(isinstance(item, bool) or not isinstance(item, int) for item in numbers)
            or payload[QDRANT_SEASON_NUMBER_FIELD] < 1
            or payload[QDRANT_EPISODE_NUMBER_FIELD] < 1
            or payload[QDRANT_CHUNK_ORDINAL_FIELD] < 0
            or payload[QDRANT_START_MS_FIELD] < 0
            or payload[QDRANT_END_MS_FIELD] <= payload[QDRANT_START_MS_FIELD]
        ):
            raise InvalidModelError(
                RetrievalErrorMessages.QDRANT_RESULT_NUMERIC_FIELDS_MUST_BE_VALID
            )
        text = payload[QDRANT_TEXT_FIELD]
        if not isinstance(text, str) or not text or text.strip() != text:
            raise InvalidModelError(RetrievalErrorMessages.QDRANT_RESULT_TEXT_MUST_BE_VALID)
        score = getattr(point, "score", missing_score)
        if isinstance(score, bool) or not isinstance(score, Real) or not math.isfinite(score):
            raise InvalidModelError(RetrievalErrorMessages.QDRANT_RESULT_SCORE_MUST_BE_FINITE)
        episode = EpisodeRef(
            series_id,
            season_id,
            episode_id,
            EpisodePosition(
                payload[QDRANT_SEASON_NUMBER_FIELD], payload[QDRANT_EPISODE_NUMBER_FIELD]
            ),
        )
        visible = next(
            (item for item in scope.episode_scopes if item.episode.episode_id == episode_id), None
        )
        if (
            visible is None
            or visible.episode != episode
            or (
                visible.safe_until_ms is not None
                and payload[QDRANT_END_MS_FIELD] > visible.safe_until_ms
            )
        ):
            raise InvalidModelError(
                RetrievalErrorMessages.QDRANT_RESULT_MUST_MATCH_VISIBILITY_SCOPE
            )
        return RetrievedSegment(
            chunk_id,
            source_version_id,
            episode,
            payload[QDRANT_START_MS_FIELD],
            payload[QDRANT_END_MS_FIELD],
            text,
            language,
            rights_status,
            float(score),
            member_ids,
            TRANSCRIPT_INDEX_REVISION,
            payload[QDRANT_CHUNK_ORDINAL_FIELD],
        )
