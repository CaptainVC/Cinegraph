from datetime import UTC, datetime
from uuid import UUID

from tests.factories import make_episode_ref

from cinegraph.adapters.repository.in_memory.in_memory_transcript_ingestion_repository import (
    InMemoryTranscriptIngestionRepository,
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
from cinegraph.domain.models.source.source_version import SourceVersion
from cinegraph.domain.models.transcript.transcript_segment import TranscriptSegment

REVIEWED_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000401")
PENDING_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000402")
REVIEWED_VERSION_ONE_ID = UUID("00000000-0000-0000-0000-000000000501")
REVIEWED_VERSION_TWO_ID = UUID("00000000-0000-0000-0000-000000000502")
PENDING_VERSION_ID = UUID("00000000-0000-0000-0000-000000000503")
TIMESTAMP = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)


def source_document(source_document_id: UUID, title: str) -> SourceDocument:
    return SourceDocument(
        source_document_id=source_document_id,
        title=title,
        kind=SourceKind.SUBTITLE,
        origin="private-local-corpus",
    )


def source_version(
    source_document_id: UUID,
    source_version_id: UUID,
    review_status: SourceReviewStatus,
) -> SourceVersion:
    final_review = review_status is SourceReviewStatus.REVIEWED
    return SourceVersion(
        source_version_id=source_version_id,
        source_document_id=source_document_id,
        content_hash=f"{source_version_id.int:064x}",
        rights_status=RightsStatus.RESTRICTED,
        acquisition_method=SourceAcquisitionMethod.LOCAL_FILESYSTEM,
        review_status=review_status,
        status=SourceVersionStatus.ACTIVE,
        acquired_at=TIMESTAMP,
        reviewed_by="local-corpus-owner" if final_review else None,
        reviewed_at=TIMESTAMP if final_review else None,
    )


def transcript_segment(
    source_version_id: UUID,
    segment_id: int,
    start_ms: int,
    end_ms: int,
) -> TranscriptSegment:
    return TranscriptSegment(
        segment_id=UUID(int=segment_id),
        source_version_id=source_version_id,
        episode=make_episode_ref(),
        start_ms=start_ms,
        end_ms=end_ms,
        text=f"Transcript segment {segment_id}.",
        language=Language.ENGLISH,
        rights_status=RightsStatus.RESTRICTED,
    )


def test_returns_only_reviewed_segments_in_timestamp_order() -> None:
    repository = InMemoryTranscriptIngestionRepository()
    reviewed_document_one = source_document(REVIEWED_DOCUMENT_ID, "Reviewed one")
    reviewed_version_one = source_version(
        REVIEWED_DOCUMENT_ID,
        REVIEWED_VERSION_ONE_ID,
        SourceReviewStatus.REVIEWED,
    )
    reviewed_document_two = source_document(PENDING_DOCUMENT_ID, "Reviewed two")
    reviewed_version_two = source_version(
        PENDING_DOCUMENT_ID,
        REVIEWED_VERSION_TWO_ID,
        SourceReviewStatus.REVIEWED,
    )
    pending_document = source_document(
        UUID("00000000-0000-0000-0000-000000000403"),
        "Pending subtitle",
    )
    pending_version = source_version(
        pending_document.source_document_id,
        PENDING_VERSION_ID,
        SourceReviewStatus.PENDING,
    )

    later_segment = transcript_segment(REVIEWED_VERSION_ONE_ID, 1, 20_000, 25_000)
    earlier_segment = transcript_segment(REVIEWED_VERSION_TWO_ID, 2, 10_000, 15_000)
    pending_segment = transcript_segment(PENDING_VERSION_ID, 3, 5_000, 8_000)

    repository.persist_new_subtitle_ingestion(
        reviewed_document_one,
        reviewed_version_one,
        None,
        (later_segment,),
    )
    repository.persist_new_subtitle_ingestion(
        reviewed_document_two,
        reviewed_version_two,
        None,
        (earlier_segment,),
    )
    repository.persist_new_subtitle_ingestion(
        pending_document,
        pending_version,
        None,
        (pending_segment,),
    )

    assert repository.get_active_reviewed_segments(make_episode_ref()) == (
        earlier_segment,
        later_segment,
    )
