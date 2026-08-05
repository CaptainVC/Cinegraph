from cinegraph.domain.enums.enum import SourceReviewStatus


FINAL_SOURCE_REVIEW_STATUSES = frozenset(
    {
        SourceReviewStatus.REVIEWED,
        SourceReviewStatus.REJECTED,
    }
)


def is_final_source_review_status(status: SourceReviewStatus) -> bool:
    return status in FINAL_SOURCE_REVIEW_STATUSES