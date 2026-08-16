from cinegraph.domain.enums.enum import SourceReviewStatus

FINAL_SOURCE_REVIEW_STATUSES = frozenset(
    {
        SourceReviewStatus.AUTOMATED_REVIEWED,
        SourceReviewStatus.HYBRID_REVIEWED,
        SourceReviewStatus.REVIEWED,
        SourceReviewStatus.REJECTED,
    }
)

APPROVED_SOURCE_REVIEW_STATUSES = frozenset(
    {
        SourceReviewStatus.AUTOMATED_REVIEWED,
        SourceReviewStatus.HYBRID_REVIEWED,
        SourceReviewStatus.REVIEWED,
    }
)


# Return whether the status is a terminal review decision.
def is_final_source_review_status(status: SourceReviewStatus) -> bool:
    return status in FINAL_SOURCE_REVIEW_STATUSES


# Return whether the source version has an approved human or automated review status.
def is_source_version_approved(status: SourceReviewStatus) -> bool:
    return status in APPROVED_SOURCE_REVIEW_STATUSES
