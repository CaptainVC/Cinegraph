from cinegraph.application.models.index_transcript_segments import (
    IndexTranscriptSegmentsCommand,
    IndexTranscriptSegmentsResult,
)
from cinegraph.common.error_messages import TranscriptErrorMessages
from cinegraph.domain.enums.enum import SourceVersionStatus
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.source.review_status import is_source_version_approved
from cinegraph.ports.retrieval.transcript_index_writer import (
    TranscriptIndexPayload,
    TranscriptIndexPoint,
    TranscriptIndexWriter,
)
from cinegraph.ports.retrieval.vector_encoder import VectorEncoder


class IndexTranscriptSegmentsService:
    # Store the encoder and governed transcript index writer dependencies.
    def __init__(
        self,
        encoder: VectorEncoder,
        writer: TranscriptIndexWriter,
    ) -> None:
        self._encoder = encoder
        self._writer = writer

    # Validate, encode, and persist approved transcript segments as one batch.
    def execute(
        self,
        command: IndexTranscriptSegmentsCommand,
    ) -> IndexTranscriptSegmentsResult:
        # Validate source governance before inspecting or encoding segments.
        if (
            command.source_version.status is not SourceVersionStatus.ACTIVE
            or not is_source_version_approved(command.source_version.review_status)
        ):
            raise InvalidModelError(
                TranscriptErrorMessages.SOURCE_VERSION_MUST_BE_ACTIVE_AND_REVIEWED
            )

        # Validate every segment reference and ID before calling the encoder.
        segment_ids = {segment.segment_id for segment in command.segments}
        if len(segment_ids) != len(command.segments):
            raise InvalidModelError(
                TranscriptErrorMessages.TRANSCRIPT_SEGMENT_IDS_MUST_BE_UNIQUE
            )
        for segment in command.segments:
            if segment.source_version_id != command.source_version.source_version_id:
                raise InvalidModelError(
                    TranscriptErrorMessages.TRANSCRIPT_SEGMENT_SOURCE_VERSION_MUST_MATCH
                )

        # Avoid encoding or writing when there are no approved segments.
        if not command.segments:
            return IndexTranscriptSegmentsResult(indexed_segment_count=0)

        points: list[TranscriptIndexPoint] = []
        for segment in command.segments:
            # Encode in caller order and map the episode and timing payload exactly.
            vector = self._encoder.encode_document(segment.text)
            payload = TranscriptIndexPayload(
                series_id=segment.episode.series_id,
                season_id=segment.episode.season_id,
                episode_id=segment.episode.episode_id,
                season_number=segment.episode.position.season_number,
                episode_number=segment.episode.position.episode_number,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                text=segment.text,
                source_status=command.source_version.status,
                review_status=command.source_version.review_status,
            )
            points.append(
                TranscriptIndexPoint(
                    segment_id=segment.segment_id,
                    vector=vector,
                    payload=payload,
                )
            )

        # Write the complete ordered batch exactly once after all points are built.
        self._writer.upsert(tuple(points))
        return IndexTranscriptSegmentsResult(
            indexed_segment_count=len(points),
        )
