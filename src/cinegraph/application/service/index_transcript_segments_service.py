from uuid import UUID

from cinegraph.application.models.index_transcript_segments import (
    IndexTranscriptSegmentsCommand,
    IndexTranscriptSegmentsResult,
)
from cinegraph.application.service.transcript_chunking_service import TranscriptChunkingService
from cinegraph.common.error_messages import TranscriptErrorMessages
from cinegraph.domain.enums.enum import RightsStatus, SourceVersionStatus
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.source.review_status import is_source_version_approved
from cinegraph.ports.retrieval.transcript_index_writer import (
    TranscriptIndexPayload,
    TranscriptIndexPoint,
    TranscriptIndexWriter,
)
from cinegraph.ports.retrieval.vector_encoder import VectorEncoder


class IndexTranscriptSegmentsService:
    def __init__(
        self,
        encoder: VectorEncoder,
        writer: TranscriptIndexWriter,
        chunker: TranscriptChunkingService | None = None,
    ) -> None:
        self._encoder = encoder
        self._writer = writer
        self._chunker = chunker or TranscriptChunkingService()

    def execute(self, command: IndexTranscriptSegmentsCommand) -> IndexTranscriptSegmentsResult:
        source = command.source_version
        if source.rights_status is not RightsStatus.ALLOWED:
            raise InvalidModelError(
                TranscriptErrorMessages.TRANSCRIPT_SOURCE_RIGHTS_STATUS_MUST_BE_ALLOWED
            )
        if source.status is not SourceVersionStatus.ACTIVE or not is_source_version_approved(
            source.review_status
        ):
            raise InvalidModelError(
                TranscriptErrorMessages.SOURCE_VERSION_MUST_BE_ACTIVE_AND_REVIEWED
            )
        seen = set()
        for segment in command.segments:
            if segment.segment_id in seen:
                raise InvalidModelError(
                    TranscriptErrorMessages.TRANSCRIPT_SEGMENT_IDS_MUST_BE_UNIQUE
                )
            seen.add(segment.segment_id)
            if segment.source_version_id != source.source_version_id:
                raise InvalidModelError(
                    TranscriptErrorMessages.TRANSCRIPT_SEGMENT_SOURCE_VERSION_MUST_MATCH
                )
            if (
                segment.rights_status is not RightsStatus.ALLOWED
                or segment.rights_status is not source.rights_status
            ):
                raise InvalidModelError(
                    TranscriptErrorMessages.TRANSCRIPT_SEGMENT_RIGHTS_STATUS_MUST_MATCH
                )

        chunks = self._chunker.chunk(command.segments)
        if not chunks:
            self._replace(source.source_version_id, source.parent_source_version_id, ())
            return IndexTranscriptSegmentsResult(input_segment_count=0, indexed_chunk_count=0)
        texts = tuple(chunk.text for chunk in chunks)
        vectors = self._encoder.encode_documents(texts)
        if len(vectors) != len(chunks):
            raise InvalidModelError(
                TranscriptErrorMessages.TRANSCRIPT_INDEX_VECTOR_CARDINALITY_MUST_MATCH
            )
        points = tuple(
            TranscriptIndexPoint(
                chunk_id=chunk.chunk_id,
                vector=vector,
                payload=TranscriptIndexPayload(
                    source_version_id=chunk.source_version_id,
                    series_id=chunk.episode.series_id,
                    season_id=chunk.episode.season_id,
                    episode_id=chunk.episode.episode_id,
                    season_number=chunk.episode.position.season_number,
                    episode_number=chunk.episode.position.episode_number,
                    start_ms=chunk.start_ms,
                    end_ms=chunk.end_ms,
                    text=chunk.text,
                    language=chunk.language,
                    rights_status=chunk.rights_status,
                    source_status=source.status,
                    review_status=source.review_status,
                    member_segment_ids=chunk.member_segment_ids,
                    chunk_ordinal=chunk.ordinal,
                    index_revision=chunk.index_revision,
                ),
            )
            for chunk, vector in zip(chunks, vectors)
        )
        self._replace(source.source_version_id, source.parent_source_version_id, points)
        return IndexTranscriptSegmentsResult(
            input_segment_count=len(command.segments), indexed_chunk_count=len(points)
        )

    def _replace(
        self,
        new_id: UUID,
        retired_id: UUID | None,
        points: tuple[TranscriptIndexPoint, ...],
    ) -> None:
        self._writer.replace_source_version(new_id, retired_id, points)
