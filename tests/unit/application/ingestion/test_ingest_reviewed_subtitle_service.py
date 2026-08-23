from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from tests.factories import FixedClock, make_episode_ref

from cinegraph.adapters.repository.in_memory.in_memory_transcript_ingestion_repository import (
    InMemoryTranscriptIngestionRepository,
)
from cinegraph.application.models.ingest_reviewed_subtitle import (
    IngestReviewedSubtitleCommand,
)
from cinegraph.application.service.ingest_reviewed_subtitle_service import (
    IngestReviewedSubtitleService,
)
from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.domain.enums.enum import (
    Language,
    RightsStatus,
    SourceAcquisitionMethod,
    SourceKind,
    SourceReviewStatus,
    SourceVersionStatus,
)
from cinegraph.domain.models.source import SourceDocument
from cinegraph.domain.models.transcript import TranscriptSegment
from cinegraph.domain.models.watch_state import EpisodeRef
from cinegraph.ingestion.transcript_srt.models import (
    TranscriptIngestionReport,
    TranscriptIngestionResult,
)

SOURCE_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000401")
SERIES_ID = UUID("00000000-0000-0000-0000-000000000011")
SEASON_ID = UUID("00000000-0000-0000-0000-000000000101")
EPISODE_ID = UUID("00000000-0000-0000-0000-000000001001")
REVIEWED_AT = datetime(2026, 7, 19, 13, 0, tzinfo=UTC)
ACQUIRED_AT = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


class InMemorySubtitleTextReader:
    def __init__(self, source_text: str) -> None:
        self.source_text = source_text
        self.call_count = 0

    def read_text(self, source_locator: str) -> str:
        self.call_count += 1
        return self.source_text


class RecordingCanonicalizer:
    def __init__(self) -> None:
        self.call_count = 0
        self.source_version_ids: list[UUID] = []

    def canonicalize(
        self,
        *,
        source_text: str,
        source_locator: str,
        source_version_id: UUID,
        episode: EpisodeRef,
        language: Language,
        rights_status: RightsStatus,
    ) -> TranscriptIngestionResult:
        self.call_count += 1
        self.source_version_ids.append(source_version_id)
        segment = TranscriptSegment(
            segment_id=IdentifierGenerator.transcript_segment_id(
                source_version_id,
                episode.episode_id,
                1,
                1_000,
                2_000,
                source_text,
            ),
            source_version_id=source_version_id,
            episode=episode,
            start_ms=1_000,
            end_ms=2_000,
            text=source_text,
            language=language,
            rights_status=rights_status,
        )
        return TranscriptIngestionResult(
            segments=(segment,),
            report=TranscriptIngestionReport(
                source_path=source_locator,
                cue_count=1,
                segment_count=1,
                multi_speaker_cue_count=0,
                overlap_count=0,
                style_removed_segment_count=0,
            ),
        )


def source_document() -> SourceDocument:
    return SourceDocument(
        source_document_id=SOURCE_DOCUMENT_ID,
        title="Modern Family S01E01 reviewed subtitle",
        kind=SourceKind.SUBTITLE,
        origin="private-local-corpus",
    )


def episode() -> EpisodeRef:
    return make_episode_ref(
        series_id=SERIES_ID,
        season_id=SEASON_ID,
        episode_id=EPISODE_ID,
    )


def command(
    review_status: SourceReviewStatus = SourceReviewStatus.REVIEWED,
) -> IngestReviewedSubtitleCommand:
    return IngestReviewedSubtitleCommand(
        source_document=source_document(),
        source_locator=str(Path("private/reviewed-s01e01.srt")),
        episode=episode(),
        language=Language.ENGLISH,
        rights_status=RightsStatus.RESTRICTED,
        acquisition_method=SourceAcquisitionMethod.LOCAL_FILESYSTEM,
        reviewed_by="local-corpus-owner",
        reviewed_at=REVIEWED_AT,
        review_status=review_status,
    )


def build_service(source_text: str):
    repository = InMemoryTranscriptIngestionRepository()
    reader = InMemorySubtitleTextReader(source_text)
    canonicalizer = RecordingCanonicalizer()
    service = IngestReviewedSubtitleService(
        repository=repository,
        subtitle_text_reader=reader,
        canonicalizer=canonicalizer,
        clock=FixedClock(ACQUIRED_AT),
    )
    return service, repository, reader, canonicalizer


def test_ingests_new_reviewed_subtitle() -> None:
    service, repository, reader, canonicalizer = build_service("CLAIRE: Hello.")

    result = service.execute(command())

    assert result.was_already_ingested is False
    assert reader.call_count == 1
    assert canonicalizer.call_count == 1
    assert canonicalizer.source_version_ids == [result.source_version.source_version_id]
    assert result.source_version.status is SourceVersionStatus.ACTIVE
    assert result.source_version.parent_source_version_id is None
    assert repository.get_active_version(SOURCE_DOCUMENT_ID) == result.source_version
    assert repository.segments == result.segments
    assert result.report is not None


def test_ingests_automated_reviewed_subtitle_with_truthful_status() -> None:
    service, _repository, _reader, _canonicalizer = build_service("CLAIRE: Hello.")

    result = service.execute(command(SourceReviewStatus.AUTOMATED_REVIEWED))

    assert result.source_version.review_status is SourceReviewStatus.AUTOMATED_REVIEWED


def test_ingests_hybrid_reviewed_subtitle_with_truthful_status() -> None:
    service, _repository, _reader, _canonicalizer = build_service("CLAIRE: Hello.")

    result = service.execute(command(SourceReviewStatus.HYBRID_REVIEWED))

    assert result.source_version.review_status is SourceReviewStatus.HYBRID_REVIEWED


def test_reingesting_unchanged_content_is_idempotent() -> None:
    service, repository, _reader, canonicalizer = build_service("CLAIRE: Hello.")

    first_result = service.execute(command())
    second_result = service.execute(command())

    assert first_result.was_already_ingested is False
    assert second_result.was_already_ingested is True
    assert second_result.source_version == first_result.source_version
    assert second_result.segments == ()
    assert second_result.report is None
    assert canonicalizer.call_count == 1
    assert len(repository.source_versions) == 1
    assert repository.segments == first_result.segments


def test_changed_content_retires_prior_version_and_links_parent() -> None:
    service, repository, reader, canonicalizer = build_service("CLAIRE: Hello.")

    first_result = service.execute(command())
    reader.source_text = "CLAIRE: Updated dialogue."
    second_result = service.execute(command())

    versions_by_id = {
        source_version.source_version_id: source_version
        for source_version in repository.source_versions
    }
    retired_version = versions_by_id[first_result.source_version.source_version_id]

    assert second_result.was_already_ingested is False
    assert second_result.source_version.source_version_id != first_result.source_version.source_version_id
    assert second_result.source_version.parent_source_version_id == first_result.source_version.source_version_id
    assert retired_version.status is SourceVersionStatus.RETIRED
    assert repository.get_active_version(SOURCE_DOCUMENT_ID) == second_result.source_version
    assert canonicalizer.call_count == 2
    assert len(repository.segments) == 2
