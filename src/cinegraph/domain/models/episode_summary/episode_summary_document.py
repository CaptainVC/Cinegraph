from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from cinegraph.common.error_messages import SummaryErrorMessages
from cinegraph.common.error_messages.source import SourceErrorMessages
from cinegraph.domain.enums.enum import Language, RightsStatus
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef


@dataclass(frozen=True, slots=True)
class EpisodeSummaryDocument:
    summary_id: UUID
    source_version_id: UUID
    episode: EpisodeRef
    text: str
    language: Language
    rights_status: RightsStatus
    canonical_url: str
    revision_id: int
    revision_timestamp: datetime
    attribution: str

    # Validates the initialized value after construction.
    def __post_init__(self) -> None:
        if not self.text or self.text.strip() != self.text:
            raise InvalidModelError(
                SummaryErrorMessages.EPISODE_SUMMARY_TEXT_MUST_BE_TRIMMED
            )

        if not isinstance(self.language, Language):
            raise InvalidModelError(
                SummaryErrorMessages.EPISODE_SUMMARY_LANGUAGE_MUST_BE_SUPPORTED
            )

        if not self.canonical_url or self.canonical_url.strip() != self.canonical_url:
            raise InvalidModelError(
                SourceErrorMessages.EPISODE_SUMMARY_CANONICAL_URL_MUST_BE_TRIMMED
            )

        if self.revision_id < 1:
            raise InvalidModelError(
                SourceErrorMessages.EPISODE_SUMMARY_REVISION_ID_MUST_BE_POSITIVE
            )

        if self.revision_timestamp.tzinfo is None:
            raise InvalidModelError(
                SourceErrorMessages.EPISODE_SUMMARY_REVISION_TIMESTAMP_MUST_BE_TIMEZONE_AWARE
            )

        if not self.attribution or self.attribution.strip() != self.attribution:
            raise InvalidModelError(
                SourceErrorMessages.EPISODE_SUMMARY_ATTRIBUTION_MUST_BE_TRIMMED
            )
