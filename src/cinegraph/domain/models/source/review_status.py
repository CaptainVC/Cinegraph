from cinegraph.domain.enums.enum import SourceReviewStatus


FINAL_SOURCE_REVIEW_STATUSES = frozenset(
    {
        SourceReviewStatus.REVIEWED,
        SourceReviewStatus.REJECTED,
    }
)


# Return whether the status is a terminal review decision.
def is_final_source_review_status(status: SourceReviewStatus) -> bool:
    return status in FINAL_SOURCE_REVIEW_STATUSES


# Return whether the source version has the approved REVIEWED status.
def is_source_version_approved(status: SourceReviewStatus) -> bool:
    return status is SourceReviewStatus.REVIEWED
