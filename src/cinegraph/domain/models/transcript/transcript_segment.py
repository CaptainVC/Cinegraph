from dataclasses import dataclass, field
from uuid import UUID

from cinegraph.common.error_messages import TranscriptErrorMessages
from cinegraph.domain.enums.enum import Language, RightsStatus
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.transcript.speaker_candidate import SpeakerCandidate
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef

@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    segment_id: UUID
    source_version_id: UUID
    episode: EpisodeRef
    start_ms: int
    end_ms: int
    text: str
    language: Language
    rights_status: RightsStatus
    style_removed: bool = False
    speaker_candidates: tuple[SpeakerCandidate, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.start_ms < 0:
            raise InvalidModelError(
                TranscriptErrorMessages.TRANSCRIPT_SEGMENT_START_MS_MUST_BE_NON_NEGATIVE
            )

        if self.end_ms <= self.start_ms:
            raise InvalidModelError(
                TranscriptErrorMessages.TRANSCRIPT_SEGMENT_END_MS_MUST_FOLLOW_START_MS

            )

        if not self.text or self.text.strip() != self.text:
            raise InvalidModelError(
                TranscriptErrorMessages.TRANSCRIPT_SEGMENT_TEXT_MUST_BE_TRIMMED
            )

        if not isinstance(self.language, Language):
            raise InvalidModelError(
            TranscriptErrorMessages.TRANSCRIPT_SEGMENT_LANGUAGE_MUST_BE_SUPPORTED
            )

        if not isinstance(self.speaker_candidates, tuple):
            raise InvalidModelError(
                TranscriptErrorMessages.TRANSCRIPT_SEGMENT_SPEAKER_CANDIDATES_MUST_BE_IMMUTABLE
            )

        normalized_names = {
            candidate.name.casefold()
            for candidate in self.speaker_candidates
        }
        if len(normalized_names) != len(self.speaker_candidates):
            raise InvalidModelError(
                TranscriptErrorMessages.TRANSCRIPT_SEGMENT_CANNOT_HAVE_DUPLICATE_SPEAKER_CANDIDATES
            )
