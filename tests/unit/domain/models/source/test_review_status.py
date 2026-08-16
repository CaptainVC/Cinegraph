from cinegraph.domain.enums.enum import SourceReviewStatus
from cinegraph.domain.models.source.review_status import (
    FINAL_SOURCE_REVIEW_STATUSES,
    is_final_source_review_status,
)


def test_final_source_review_statuses_contain_only_terminal_decisions() -> None:
    assert FINAL_SOURCE_REVIEW_STATUSES == frozenset(
        {
            SourceReviewStatus.AUTOMATED_REVIEWED,
            SourceReviewStatus.REVIEWED,
            SourceReviewStatus.REJECTED,
        }
    )
    assert is_final_source_review_status(SourceReviewStatus.AUTOMATED_REVIEWED)
    assert is_final_source_review_status(SourceReviewStatus.REVIEWED)
    assert is_final_source_review_status(SourceReviewStatus.REJECTED)
    assert not is_final_source_review_status(SourceReviewStatus.PENDING)
