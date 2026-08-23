from dataclasses import dataclass
from datetime import datetime
from re import fullmatch
from uuid import UUID

from cinegraph.common.error_messages import SourceErrorMessages
from cinegraph.domain.enums.enum import (
    RightsStatus,
    SourceAcquisitionMethod,
    SourceReviewStatus,
    SourceVersionStatus,
)
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.source.review_status import is_final_source_review_status


@dataclass(frozen=True, slots=True)
class SourceVersion:
    source_version_id: UUID
    source_document_id: UUID
    content_hash: str
    rights_status: RightsStatus
    acquisition_method: SourceAcquisitionMethod
    review_status: SourceReviewStatus
    status: SourceVersionStatus
    acquired_at: datetime
    parent_source_version_id: UUID | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None

    # Enforce content-hash, status, timestamp, and review-metadata invariants.
    def __post_init__(self) -> None:
        if fullmatch(r"[0-9a-f]{64}", self.content_hash) is None:
            raise InvalidModelError(
                SourceErrorMessages.SOURCE_VERSION_CONTENT_HASH_MUST_BE_SHA256
            )

        if self.acquired_at.tzinfo is None:
            raise InvalidModelError(
                SourceErrorMessages.SOURCE_VERSION_ACQUIRED_AT_MUST_BE_TIMEZONE_AWARE
            )

        if self.reviewed_at is not None and self.reviewed_at.tzinfo is None:
            raise InvalidModelError(
                SourceErrorMessages.SOURCE_VERSION_REVIEWED_AT_MUST_BE_TIMEZONE_AWARE
            )

        if self.parent_source_version_id == self.source_version_id:
            raise InvalidModelError(
                SourceErrorMessages.SOURCE_VERSION_PARENT_CANNOT_EQUAL_SELF
            )

        self.validate_review_metadata()

    # Validate reviewer metadata required by the current review status.
    def validate_review_metadata(self) -> None:

        has_reviewer = self.reviewed_by is not None
        was_reviewed_at = self.reviewed_at is not None

        if is_final_source_review_status(self.review_status):
            if not has_reviewer or not was_reviewed_at:
                raise InvalidModelError(
                    SourceErrorMessages.SOURCE_VERSION_REVIEWED_REQUIRES_REVIEW_METADATA
                )

            if (
                not self.reviewed_by
                or self.reviewed_by.strip() != self.reviewed_by
            ):
                raise InvalidModelError(
                    SourceErrorMessages.SOURCE_VERSION_REVIEWER_MUST_BE_TRIMMED
                )
            return

        if has_reviewer or was_reviewed_at:
            raise InvalidModelError(
                SourceErrorMessages.SOURCE_VERSION_NON_REVIEWED_CANNOT_HAVE_REVIEW_METADATA
            )
