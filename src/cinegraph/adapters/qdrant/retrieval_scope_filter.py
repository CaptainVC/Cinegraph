from cinegraph.domain.enums.enum import SourceReviewStatus, SourceVersionStatus
from cinegraph.domain.models.source.review_status import (
    APPROVED_SOURCE_REVIEW_STATUSES,
)
from cinegraph.domain.retrieval.retrieval_scope import RetrievalScope
from qdrant_client.http import models


_SERIES_ID_FIELD = "series_id"
_EPISODE_ID_FIELD = "episode_id"
_END_MS_FIELD = "end_ms"
_SOURCE_STATUS_FIELD = "source_status"
_REVIEW_STATUS_FIELD = "review_status"


# Compile visible episode scopes into a Qdrant filter with per-episode time bounds.
def compile_retrieval_scope_filter(
    scope: RetrievalScope,
) -> models.Filter | None:
    # Avoid emitting a filter that could match documents when no episode is visible.
    if not scope.episode_scopes:
        return None

    # Build one episode-specific visibility branch for each permitted scope.
    visibility_filters = []
    for episode_scope in scope.episode_scopes:
        conditions = [
            models.FieldCondition(
                key=_EPISODE_ID_FIELD,
                match=models.MatchValue(value=str(episode_scope.episode.episode_id)),
            )
        ]
        if episode_scope.safe_until_ms is not None:
            conditions.append(
                models.FieldCondition(
                    key=_END_MS_FIELD,
                    range=models.Range(lte=episode_scope.safe_until_ms),
                )
            )
        visibility_filters.append(models.Filter(must=conditions))

    # Combine series, source, review, and episode visibility constraints.
    return models.Filter(
        must=[
            models.FieldCondition(
                key=_SERIES_ID_FIELD,
                match=models.MatchValue(value=str(scope.series_id)),
            ),
            models.FieldCondition(
                key=_SOURCE_STATUS_FIELD,
                match=models.MatchValue(value=SourceVersionStatus.ACTIVE.value),
            ),
            models.FieldCondition(
                key=_REVIEW_STATUS_FIELD,
                match=models.MatchAny(
                    any=[
                        status.value
                        for status in SourceReviewStatus
                        if status in APPROVED_SOURCE_REVIEW_STATUSES
                    ]
                ),
            ),
            models.Filter(should=visibility_filters),
        ]
    )
