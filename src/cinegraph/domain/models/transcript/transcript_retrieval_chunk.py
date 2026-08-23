from dataclasses import dataclass
from uuid import UUID

from cinegraph.common.error_messages import TranscriptErrorMessages
from cinegraph.domain.enums.enum import Language, RightsStatus
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef


@dataclass(frozen=True, slots=True)
class TranscriptRetrievalChunk:
    chunk_id: UUID
    source_version_id: UUID
    episode: EpisodeRef
    ordinal: int
    member_segment_ids: tuple[UUID, ...]
    start_ms: int
    end_ms: int
    text: str
    language: Language
    rights_status: RightsStatus
    index_revision: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.chunk_id, UUID)
            or not isinstance(self.source_version_id, UUID)
            or not isinstance(self.episode, EpisodeRef)
        ):
            raise InvalidModelError(
                TranscriptErrorMessages.TRANSCRIPT_CHUNK_SOURCE_FIELDS_MUST_BE_VALID
            )
        if (
            not isinstance(self.member_segment_ids, tuple)
            or not self.member_segment_ids
            or any(not isinstance(item, UUID) for item in self.member_segment_ids)
            or len(set(self.member_segment_ids)) != len(self.member_segment_ids)
        ):
            raise InvalidModelError(
                TranscriptErrorMessages.TRANSCRIPT_CHUNK_SEGMENTS_MUST_BE_NONEMPTY_UNIQUE
            )
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 0:
            raise InvalidModelError(
                TranscriptErrorMessages.TRANSCRIPT_CHUNK_ORDINAL_MUST_BE_NON_NEGATIVE
            )
        if (
            isinstance(self.start_ms, bool)
            or not isinstance(self.start_ms, int)
            or isinstance(self.end_ms, bool)
            or not isinstance(self.end_ms, int)
            or self.start_ms < 0
            or self.end_ms <= self.start_ms
        ):
            raise InvalidModelError(TranscriptErrorMessages.TRANSCRIPT_CHUNK_TIMING_MUST_BE_VALID)
        if not isinstance(self.text, str) or not self.text or self.text.strip() != self.text:
            raise InvalidModelError(TranscriptErrorMessages.TRANSCRIPT_CHUNK_TEXT_MUST_BE_TRIMMED)
        if (
            not isinstance(self.language, Language)
            or not isinstance(self.rights_status, RightsStatus)
            or not isinstance(self.index_revision, str)
            or not self.index_revision
            or self.index_revision.strip() != self.index_revision
        ):
            raise InvalidModelError(
                TranscriptErrorMessages.TRANSCRIPT_CHUNK_SOURCE_FIELDS_MUST_BE_VALID
            )
