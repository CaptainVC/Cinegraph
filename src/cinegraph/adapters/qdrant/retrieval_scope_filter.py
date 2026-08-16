from cinegraph.domain.enums.enum import SourceReviewStatus, SourceVersionStatus
from cinegraph.domain.models.source.review_status import (
    APPROVED_SOURCE_REVIEW_STATUSES,
)
from cinegraph.domain.retrieval.retrieval_scope import RetrievalScope
from qdrant_client.http import models
from cinegraph.config.qdrant import (
    QDRANT_END_MS_FIELD,
    QDRANT_EPISODE_ID_FIELD,
    QDRANT_REVIEW_STATUS_FIELD,
    QDRANT_SERIES_ID_FIELD,
    QDRANT_SOURCE_STATUS_FIELD,
)


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
                key=QDRANT_EPISODE_ID_FIELD,
                match=models.MatchValue(value=str(episode_scope.episode.episode_id)),
            )
        ]
        if episode_scope.safe_until_ms is not None:
            conditions.append(
                models.FieldCondition(
                    key=QDRANT_END_MS_FIELD,
                    range=models.Range(lte=episode_scope.safe_until_ms),
                )
            )
        visibility_filters.append(models.Filter(must=conditions))

    # Combine series, source, review, and episode visibility constraints.
    return models.Filter(
        must=[
            models.FieldCondition(
                key=QDRANT_SERIES_ID_FIELD,
                match=models.MatchValue(value=str(scope.series_id)),
            ),
            models.FieldCondition(
                key=QDRANT_SOURCE_STATUS_FIELD,
                match=models.MatchValue(value=SourceVersionStatus.ACTIVE.value),
            ),
            models.FieldCondition(
                key=QDRANT_REVIEW_STATUS_FIELD,
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
