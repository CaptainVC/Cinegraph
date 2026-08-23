from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from tests.factories import make_episode_ref

from cinegraph.adapters.date_time.system_clock import SystemClock
from cinegraph.adapters.ingestion.finalized_srt_canonicalizer import (
    FinalizedSrtCanonicalizer,
)
from cinegraph.adapters.repository.in_memory.in_memory_transcript_ingestion_repository import (
    InMemoryTranscriptIngestionRepository,
)
from cinegraph.adapters.source.local_subtitle_text_reader import LocalSubtitleTextReader
from cinegraph.application.models.ingest_reviewed_corpus import (
    IngestReviewedCorpusCommand,
    ReviewedSubtitleBatch,
    ReviewedSubtitleBatchItem,
)
from cinegraph.application.service.index_transcript_segments_service import (
    IndexTranscriptSegmentsService,
)
from cinegraph.application.service.ingest_reviewed_corpus_service import (
    IngestReviewedCorpusService,
)
from cinegraph.application.service.ingest_reviewed_subtitle_service import (
    IngestReviewedSubtitleService,
)
from cinegraph.domain.enums.enum import SourceReviewStatus
from cinegraph.domain.retrieval import (
    DenseVector,
    DocumentVector,
    HybridVector,
    SparseVector,
)
from cinegraph.ports.retrieval import TranscriptIndexPoint


class FixedEncoder:
    def encode_document(self, text: str) -> DocumentVector:
        return DocumentVector(HybridVector(DenseVector((0.5,)), SparseVector((1,), (1.0,))))

    def encode_documents(self, texts: tuple[str, ...]) -> tuple[DocumentVector, ...]:
        return tuple(self.encode_document(text) for text in texts)


class RecordingWriter:
    def __init__(self) -> None:
        self.batches: list[tuple[TranscriptIndexPoint, ...]] = []

    def replace_source_version(
        self,
        new_source_version_id,
        retired_source_version_id,
        points: tuple[TranscriptIndexPoint, ...],
    ) -> None:
        self.batches.append(points)


def test_corpus_orchestration_is_deterministic_and_skips_duplicate_reindex(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "episode.reviewed.srt"
    source_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nClaire: Hello.\n",
        encoding="utf-8",
    )
    content_hash = sha256(source_path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    item = ReviewedSubtitleBatchItem(
        episode=make_episode_ref(episode_id=UUID(int=1001)),
        episode_title="Example Family: Pilot",
        source_path=source_path,
        content_sha256=content_hash,
        reviewed_by="corpus-review",
        reviewed_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
        review_status=SourceReviewStatus.REVIEWED,
    )
    repository = InMemoryTranscriptIngestionRepository()
    writer = RecordingWriter()
    service = IngestReviewedCorpusService(
        IngestReviewedSubtitleService(
            repository,
            LocalSubtitleTextReader(),
            FinalizedSrtCanonicalizer(),
            SystemClock(),
        ),
        IndexTranscriptSegmentsService(FixedEncoder(), writer),
    )
    command = IngestReviewedCorpusCommand(ReviewedSubtitleBatch((item,)))

    first = service.execute(command)
    second = service.execute(command)

    assert first.outcomes[0].segment_count == 1
    assert first.outcomes[0].indexed_segment_count == 1
    assert first.outcomes[0].was_already_ingested is False
    assert second.outcomes[0].was_already_ingested is True
    assert second.outcomes[0].indexed_segment_count == 0
    assert second.outcomes[0].source_version_id == first.outcomes[0].source_version_id
    assert len(writer.batches) == 1
