from dataclasses import dataclass
from math import isfinite
from uuid import UUID

from cinegraph.common.error_messages import TranscriptErrorMessages
from cinegraph.domain.exceptions.errors import InvalidModelError


@dataclass(frozen=True, slots=True)
class SpeakerCandidate:
    speaker_id: UUID
    name: str
    confidence: float

    # Require a trimmed speaker name and a confidence value within the valid range.
    def __post_init__(self) -> None:
        if not self.name or self.name.strip() != self.name:
            raise InvalidModelError(
                TranscriptErrorMessages.SPEAKER_CANDIDATE_NAME_MUST_BE_TRIMMED
            )

        if (
            not isfinite(self.confidence)
            or self.confidence < 0.0
            or self.confidence > 1.0
        ):
            raise InvalidModelError(
                TranscriptErrorMessages.SPEAKER_CANDIDATE_CONFIDENCE_MUST_BE_FINITE
            )
