from cinegraph.application.exceptions.errors import SourceVersionNotFoundError
from cinegraph.application.models.review_episode_summary import ReviewEpisodeSummaryCommand, ReviewEpisodeSummaryResult
from cinegraph.common.error_messages import SourceErrorMessages
from cinegraph.domain.models.source.review_status import is_final_source_review_status
from cinegraph.ports.repository.episode_summary_ingestion_repository import EpisodeSummaryIngestionRepository

class ReviewEpisodeSummaryService:

    # Store the summary ingestion repository used for review transitions.
    def __init__(
            self,
            repository: EpisodeSummaryIngestionRepository,
    ) -> None:
        self._repository = repository

    # Validate and persist a final summary review decision, preserving idempotent repeats.
    def execute(
            self,
            command: ReviewEpisodeSummaryCommand,
    ) -> ReviewEpisodeSummaryResult:

        # 1. Validate the requested final review decision.
        if not is_final_source_review_status(command.review_status):
            raise ValueError(
                SourceErrorMessages.SOURCE_VERSION_REVIEW_REQUIRES_FINAL_DECISION
            )

        # 2. Load the source version being reviewed.
        source_version = self._repository.get_source_version(command.source_version_id)
        if source_version is None:
            raise SourceVersionNotFoundError(command.source_version_id)

        # 3. Return an idempotent result for the same decision.
        if source_version.review_status is command.review_status:
            return ReviewEpisodeSummaryResult(
                source_version=source_version,
                was_already_reviewed=True,
            )

        # 4. Persist the review decision and reviewer metadata.
        updated_source_version = self._repository.update_source_version_review_status(
            source_version_id=command.source_version_id,
            review_status=command.review_status,
            reviewed_by=command.reviewed_by,
            reviewed_at=command.reviewed_at,
        )

        # 5. Return the updated immutable source version.
        return ReviewEpisodeSummaryResult(
            source_version=updated_source_version,
            was_already_reviewed=False,
        )
