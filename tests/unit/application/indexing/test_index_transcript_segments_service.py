from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from cinegraph.application.models.index_transcript_segments import (
    IndexTranscriptSegmentsCommand,
)
from cinegraph.application.service.index_transcript_segments_service import (
    IndexTranscriptSegmentsService,
)
from cinegraph.common.error_messages import TranscriptErrorMessages
from cinegraph.domain.enums.enum import (
    Language,
    RightsStatus,
    SourceAcquisitionMethod,
    SourceReviewStatus,
    SourceVersionStatus,
)
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.source import SourceVersion
from cinegraph.domain.models.transcript import TranscriptSegment
from cinegraph.domain.models.watch_state import EpisodeRef
from cinegraph.domain.retrieval.vector_data import (
    DenseVector,
    DocumentVector,
    HybridVector,
    SparseVector,
)
from cinegraph.ports.retrieval import TranscriptIndexPoint
from tests.factories import make_episode_ref


SOURCE_VERSION_ID = UUID("00000000-0000-0000-0000-000000000701")


class RecordingEncoder:
    # Record document text and return one valid document vector.
    def __init__(self) -> None:
        self.texts: list[str] = []
        vector = HybridVector(DenseVector((0.5,)), SparseVector((1,), (1.0,)))
        self.document_vector = DocumentVector(vector)

    # Record one document encoding request.
    def encode_document(self, text: str) -> DocumentVector:
        self.texts.append(text)
        return self.document_vector


class RecordingWriter:
    # Record every writer batch received by the service.
    def __init__(self) -> None:
        self.batches: list[tuple[TranscriptIndexPoint, ...]] = []

    # Record one complete upsert batch.
    def upsert(self, points: tuple[TranscriptIndexPoint, ...]) -> None:
        self.batches.append(points)


def make_source_version(
    *,
    status: SourceVersionStatus = SourceVersionStatus.ACTIVE,
    review_status: SourceReviewStatus = SourceReviewStatus.REVIEWED,
) -> SourceVersion:
    # Build a source version with metadata valid for the requested lifecycle state.
    reviewed = review_status in (
        SourceReviewStatus.AUTOMATED_REVIEWED,
        SourceReviewStatus.REVIEWED,
        SourceReviewStatus.REJECTED,
    )
    return SourceVersion(
        source_version_id=SOURCE_VERSION_ID,
        source_document_id=uuid4(),
        content_hash="a" * 64,
        rights_status=RightsStatus.ALLOWED,
        acquisition_method=SourceAcquisitionMethod.SYNTHETIC_FIXTURE,
        review_status=review_status,
        status=status,
        acquired_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
        reviewed_by="test-reviewer" if reviewed else None,
        reviewed_at=datetime(2026, 7, 19, 13, 0, tzinfo=UTC) if reviewed else None,
    )


def make_segment(
    source_version_id: UUID = SOURCE_VERSION_ID,
    *,
    text: str = "Claire asks about dinner",
    start_ms: int = 1_000,
    episode: EpisodeRef | None = None,
) -> TranscriptSegment:
    # Build a valid transcript segment for the supplied source and episode.
    return TranscriptSegment(
        segment_id=uuid4(),
        source_version_id=source_version_id,
        episode=episode or make_episode_ref(season_number=1, episode_number=2),
        start_ms=start_ms,
        end_ms=start_ms + 500,
        text=text,
        language=Language.ENGLISH,
        rights_status=RightsStatus.ALLOWED,
    )


def make_service() -> tuple[IndexTranscriptSegmentsService, RecordingEncoder, RecordingWriter]:
    # Build the service with recording ports for behavior assertions.
    encoder = RecordingEncoder()
    writer = RecordingWriter()
    return IndexTranscriptSegmentsService(encoder, writer), encoder, writer


def test_active_reviewed_segments_index_in_input_order_with_one_batch() -> None:
    # Verify encoding order, payload mapping, point IDs, and one writer call.
    service, encoder, writer = make_service()
    source = make_source_version()
    first = make_segment(text="First line", start_ms=100)
    second = make_segment(text="Second line", start_ms=200)

    result = service.execute(IndexTranscriptSegmentsCommand(source, (first, second)))

    assert result.indexed_segment_count == 2
    assert encoder.texts == ["First line", "Second line"]
    assert len(writer.batches) == 1
    points = writer.batches[0]
    assert [point.segment_id for point in points] == [first.segment_id, second.segment_id]
    assert [point.vector for point in points] == [encoder.document_vector] * 2
    assert points[0].payload.series_id == first.episode.series_id
    assert points[0].payload.season_id == first.episode.season_id
    assert points[0].payload.episode_id == first.episode.episode_id
    assert points[0].payload.season_number == 1
    assert points[0].payload.episode_number == 2
    assert points[0].payload.start_ms == first.start_ms
    assert points[0].payload.end_ms == first.end_ms
    assert points[0].payload.text == first.text
    assert points[0].payload.source_status is SourceVersionStatus.ACTIVE
    assert points[0].payload.review_status is SourceReviewStatus.REVIEWED


def test_active_automated_reviewed_segments_are_approved_for_indexing() -> None:
    service, encoder, writer = make_service()
    source = make_source_version(
        review_status=SourceReviewStatus.AUTOMATED_REVIEWED
    )

    result = service.execute(
        IndexTranscriptSegmentsCommand(source, (make_segment(),))
    )

    assert result.indexed_segment_count == 1
    assert encoder.texts == ["Claire asks about dinner"]
    assert writer.batches[0][0].payload.review_status is (
        SourceReviewStatus.AUTOMATED_REVIEWED
    )


@pytest.mark.parametrize(
    ("status", "review_status"),
    [
        (SourceVersionStatus.ACTIVE, SourceReviewStatus.PENDING),
        (SourceVersionStatus.ACTIVE, SourceReviewStatus.REJECTED),
        (SourceVersionStatus.RETIRED, SourceReviewStatus.REVIEWED),
    ],
)
def test_unapproved_source_rejects_before_encoder_or_writer(
    status: SourceVersionStatus,
    review_status: SourceReviewStatus,
) -> None:
    # Verify every non-approved source is rejected before side effects.
    service, encoder, writer = make_service()

    with pytest.raises(
        InvalidModelError,
        match=TranscriptErrorMessages.SOURCE_VERSION_MUST_BE_ACTIVE_AND_REVIEWED,
    ):
        service.execute(
            IndexTranscriptSegmentsCommand(
                make_source_version(status=status, review_status=review_status),
                (make_segment(),),
            )
        )

    assert encoder.texts == []
    assert writer.batches == []


def test_wrong_source_segment_rejects_before_encoder_or_writer() -> None:
    # Verify source ownership is checked before any dependency call.
    service, encoder, writer = make_service()

    with pytest.raises(
        InvalidModelError,
        match=TranscriptErrorMessages.TRANSCRIPT_SEGMENT_SOURCE_VERSION_MUST_MATCH,
    ):
        service.execute(
            IndexTranscriptSegmentsCommand(
                make_source_version(),
                (make_segment(uuid4()),),
            )
        )

    assert encoder.texts == []
    assert writer.batches == []


def test_duplicate_segment_ids_reject_before_encoder_or_writer() -> None:
    # Verify duplicate IDs are rejected before any dependency call.
    service, encoder, writer = make_service()
    first = make_segment()
    duplicate = TranscriptSegment(
        segment_id=first.segment_id,
        source_version_id=first.source_version_id,
        episode=first.episode,
        start_ms=first.start_ms + 1_000,
        end_ms=first.end_ms + 1_000,
        text="Duplicate ID",
        language=first.language,
        rights_status=first.rights_status,
    )

    with pytest.raises(
        InvalidModelError,
        match=TranscriptErrorMessages.TRANSCRIPT_SEGMENT_IDS_MUST_BE_UNIQUE,
    ):
        service.execute(
            IndexTranscriptSegmentsCommand(make_source_version(), (first, duplicate))
        )

    assert encoder.texts == []
    assert writer.batches == []


def test_empty_approved_segments_return_zero_without_calls() -> None:
    # Verify an approved empty input performs no encoding or writing.
    service, encoder, writer = make_service()

    result = service.execute(
        IndexTranscriptSegmentsCommand(make_source_version(), ())
    )

    assert result.indexed_segment_count == 0
    assert encoder.texts == []
    assert writer.batches == []
