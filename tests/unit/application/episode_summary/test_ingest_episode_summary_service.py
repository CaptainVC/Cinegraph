from datetime import UTC, datetime
from uuid import UUID

from tests.factories import make_episode_ref

from cinegraph.adapters.repository.in_memory.in_memory_episode_summary_ingestion_repository import (
    InMemoryEpisodeSummaryIngestionRepository,
)
from cinegraph.application.service.ingest_episode_summary_service import (
    IngestEpisodeSummaryService,
)
from cinegraph.domain.enums.enum import (
    Language,
    RightsStatus,
    SourceAcquisitionMethod,
    SourceKind,
    SourceReviewStatus,
    SourceVersionStatus,
)
from cinegraph.domain.models.source.source_document import SourceDocument
from cinegraph.ingestion.episode_summary.ingest_episode_summary import (
    IngestEpisodeSummaryCommand,
)
from cinegraph.ports.dto.fetched_episode_summary import FetchedEpisodeSummary

SOURCE_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000401")
RETRIEVED_AT = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


class StubEpisodeSummaryProvider:
    def __init__(self, text: str) -> None:
        self.text = text
        self.call_count = 0

    def fetch(
        self,
        *,
        page_title: str,
        language: Language,
    ) -> FetchedEpisodeSummary:
        self.call_count += 1

        return FetchedEpisodeSummary(
            page_title=page_title,
            canonical_url="https://en.wikipedia.org/wiki/Pilot_(Modern_Family)",
            revision_id=123,
            revision_timestamp=RETRIEVED_AT,
            retrieved_at=RETRIEVED_AT,
            text=self.text,
            language=language,
            attribution="Wikipedia contributors, CC BY-SA",
        )


def source_document() -> SourceDocument:
    return SourceDocument(
        source_document_id=SOURCE_DOCUMENT_ID,
        title="Modern Family S01E01 summary",
        kind=SourceKind.EPISODE_PLOT,
        origin="wikipedia",
    )


def command() -> IngestEpisodeSummaryCommand:
    return IngestEpisodeSummaryCommand(
        source_document=source_document(),
        page_title="Pilot (Modern Family)",
        episode=make_episode_ref(),
        language=Language.ENGLISH,
        rights_status=RightsStatus.ALLOWED,
    )


def build_service(text: str):
    provider = StubEpisodeSummaryProvider(text)
    repository = InMemoryEpisodeSummaryIngestionRepository()
    service = IngestEpisodeSummaryService(
        provider=provider,
        repository=repository,
    )
    return service, provider, repository


def test_ingests_a_new_episode_summary() -> None:
    service, provider, repository = build_service("A concise episode summary.")

    result = service.execute(command())

    assert provider.call_count == 1
    assert result.was_already_ingested is False
    assert result.summary is not None
    assert result.source_version.status is SourceVersionStatus.ACTIVE
    assert result.source_version.review_status is SourceReviewStatus.PENDING
    assert result.source_version.acquisition_method is SourceAcquisitionMethod.MEDIAWIKI_API
    assert repository.get_active_version(SOURCE_DOCUMENT_ID) == result.source_version
    assert repository.summaries == (result.summary,)


def test_reingesting_identical_summary_is_idempotent() -> None:
    service, provider, repository = build_service("A concise episode summary.")

    first_result = service.execute(command())
    second_result = service.execute(command())

    assert provider.call_count == 2
    assert first_result.was_already_ingested is False
    assert second_result.was_already_ingested is True
    assert second_result.source_version == first_result.source_version
    assert second_result.summary is None
    assert repository.source_versions == (first_result.source_version,)
    assert repository.summaries == (first_result.summary,)


def test_changed_summary_retires_previous_active_version() -> None:
    service, provider, repository = build_service("First summary.")

    first_result = service.execute(command())
    provider.text = "Updated summary."
    second_result = service.execute(command())

    versions_by_id = {
        version.source_version_id: version
        for version in repository.source_versions
    }

    assert second_result.was_already_ingested is False
    assert second_result.summary is not None
    assert (
        second_result.source_version.parent_source_version_id
        == first_result.source_version.source_version_id
    )
    assert (
        versions_by_id[first_result.source_version.source_version_id].status
        is SourceVersionStatus.RETIRED
    )
    assert repository.get_active_version(SOURCE_DOCUMENT_ID) == second_result.source_version
    assert len(repository.summaries) == 2
