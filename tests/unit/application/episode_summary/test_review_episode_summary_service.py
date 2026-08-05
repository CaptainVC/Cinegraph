from datetime import UTC, datetime
from uuid import UUID

import pytest

from cinegraph.adapters.repository.in_memory.in_memory_episode_summary_ingestion_repository import (
    InMemoryEpisodeSummaryIngestionRepository,
)
from cinegraph.application.exceptions.errors import SourceVersionNotFoundError
from cinegraph.application.models.review_episode_summary import (
    ReviewEpisodeSummaryCommand,
)
from cinegraph.application.service.review_episode_summary_service import (
    ReviewEpisodeSummaryService,
)
from cinegraph.domain.enums.enum import (
    Language,
    RightsStatus,
    SourceAcquisitionMethod,
    SourceKind,
    SourceReviewStatus,
    SourceVersionStatus,
)
from cinegraph.domain.models.episode_summary.episode_summary_document import (
    EpisodeSummaryDocument,
)
from cinegraph.domain.models.source.source_document import SourceDocument
from cinegraph.domain.models.source.source_version import SourceVersion
from tests.factories import make_episode_ref


SOURCE_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000401")
SOURCE_VERSION_ID = UUID("00000000-0000-0000-0000-000000000501")
SUMMARY_ID = UUID("00000000-0000-0000-0000-000000000601")
ACQUIRED_AT = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
REVIEWED_AT = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)


def source_document() -> SourceDocument:
    return SourceDocument(
        source_document_id=SOURCE_DOCUMENT_ID,
        title="Modern Family S01E01 summary",
        kind=SourceKind.EPISODE_PLOT,
        origin="wikipedia",
    )


def pending_source_version() -> SourceVersion:
    return SourceVersion(
        source_version_id=SOURCE_VERSION_ID,
        source_document_id=SOURCE_DOCUMENT_ID,
        content_hash="a" * 64,
        rights_status=RightsStatus.ALLOWED,
        acquisition_method=SourceAcquisitionMethod.MEDIAWIKI_API,
        review_status=SourceReviewStatus.PENDING,
        status=SourceVersionStatus.ACTIVE,
        acquired_at=ACQUIRED_AT,
    )


def episode_summary() -> EpisodeSummaryDocument:
    episode = make_episode_ref()
    return EpisodeSummaryDocument(
        summary_id=SUMMARY_ID,
        source_version_id=SOURCE_VERSION_ID,
        episode=episode,
        text="A concise episode summary.",
        language=Language.ENGLISH,
        rights_status=RightsStatus.ALLOWED,
        canonical_url="https://en.wikipedia.org/wiki/Pilot_(Modern_Family)",
        revision_id=123,
        revision_timestamp=ACQUIRED_AT,
        attribution="Wikipedia contributors, CC BY-SA",
    )


def repository_with_pending_summary() -> tuple[
    InMemoryEpisodeSummaryIngestionRepository,
    SourceVersion,
]:
    repository = InMemoryEpisodeSummaryIngestionRepository()
    source_version = pending_source_version()
    repository.persist_new_episode_summary_ingestion(
        source_document=source_document(),
        source_version=source_version,
        previous_active_version=None,
        summary=episode_summary(),
    )
    return repository, source_version


def review_command(
    review_status: SourceReviewStatus = SourceReviewStatus.REVIEWED,
) -> ReviewEpisodeSummaryCommand:
    return ReviewEpisodeSummaryCommand(
        source_version_id=SOURCE_VERSION_ID,
        review_status=review_status,
        reviewed_by="local-corpus-owner",
        reviewed_at=REVIEWED_AT,
    )


def test_approves_pending_episode_summary() -> None:
    repository, pending_version = repository_with_pending_summary()
    service = ReviewEpisodeSummaryService(repository)

    result = service.execute(review_command())

    assert result.was_already_reviewed is False
    assert result.source_version.source_version_id == pending_version.source_version_id
    assert result.source_version.review_status is SourceReviewStatus.REVIEWED
    assert result.source_version.reviewed_by == "local-corpus-owner"
    assert result.source_version.reviewed_at == REVIEWED_AT
    assert repository.get_source_version(SOURCE_VERSION_ID) == result.source_version


def test_repeating_same_review_decision_is_idempotent() -> None:
    repository, _pending_version = repository_with_pending_summary()
    service = ReviewEpisodeSummaryService(repository)

    first_result = service.execute(review_command())
    second_result = service.execute(review_command())

    assert first_result.was_already_reviewed is False
    assert second_result.was_already_reviewed is True
    assert second_result.source_version == first_result.source_version


def test_rejects_pending_episode_summary() -> None:
    repository, _pending_version = repository_with_pending_summary()
    service = ReviewEpisodeSummaryService(repository)

    result = service.execute(review_command(SourceReviewStatus.REJECTED))

    assert result.was_already_reviewed is False
    assert result.source_version.review_status is SourceReviewStatus.REJECTED
    assert result.source_version.reviewed_by == "local-corpus-owner"


def test_missing_source_version_raises_typed_error() -> None:
    service = ReviewEpisodeSummaryService(
        InMemoryEpisodeSummaryIngestionRepository()
    )

    with pytest.raises(SourceVersionNotFoundError):
        service.execute(
            ReviewEpisodeSummaryCommand(
                source_version_id=UUID("00000000-0000-0000-0000-000000009999"),
                review_status=SourceReviewStatus.REVIEWED,
                reviewed_by="local-corpus-owner",
                reviewed_at=REVIEWED_AT,
            )
        )


def test_rejects_non_final_review_decision() -> None:
    repository, _pending_version = repository_with_pending_summary()
    service = ReviewEpisodeSummaryService(repository)

    with pytest.raises(ValueError, match="reviewed or rejected"):
        service.execute(review_command(SourceReviewStatus.PENDING))
