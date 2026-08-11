from cinegraph.domain.enums.enum import SourceReviewStatus


FINAL_SOURCE_REVIEW_STATUSES = frozenset(
    {
        SourceReviewStatus.REVIEWED,
        SourceReviewStatus.REJECTED,
    }
)


# Checks whether the supplied value satisfies the condition.
def is_final_source_review_status(status: SourceReviewStatus) -> bool:
    return status in FINAL_SOURCE_REVIEW_STATUSES


# Checks whether the supplied value satisfies the condition.
def is_source_version_approved(status: SourceReviewStatus) -> bool:
    return status is SourceReviewStatus.REVIEWED
