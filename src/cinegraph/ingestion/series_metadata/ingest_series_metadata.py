from dataclasses import dataclass
from uuid import UUID

from cinegraph.common.error_messages.source import SourceErrorMessages
from cinegraph.domain.enums.enum import RightsStatus, SourceKind
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.series_metadata import SeriesMetadataSnapshot
from cinegraph.domain.models.source.source_document import SourceDocument
from cinegraph.domain.models.source.source_version import SourceVersion
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef


@dataclass(frozen=True, slots=True)
class IngestSeriesMetadataCommand:
    source_document: SourceDocument
    series_id: UUID
    provider_show_id: int
    expected_title: str
    episodes: tuple[EpisodeRef, ...]
    rights_status: RightsStatus = RightsStatus.ALLOWED

    def __post_init__(self) -> None:
        if self.source_document.kind is not SourceKind.METADATA:
            raise InvalidModelError(
                SourceErrorMessages.SERIES_METADATA_SOURCE_DOCUMENT_MUST_BE_METADATA
            )
        if (
            not isinstance(self.series_id, UUID)
            or not isinstance(self.provider_show_id, int)
            or isinstance(self.provider_show_id, bool)
            or self.series_id.int == 0
            or self.provider_show_id < 1
        ):
            raise InvalidModelError(SourceErrorMessages.TVMAZE_SHOW_ID_MUST_BE_POSITIVE)
        if (
            not isinstance(self.expected_title, str)
            or not self.expected_title
            or self.expected_title.strip() != self.expected_title
        ):
            raise InvalidModelError(SourceErrorMessages.METADATA_VALUE_MUST_BE_TRIMMED)
        if not self.episodes:
            raise InvalidModelError(
                SourceErrorMessages.SERIES_METADATA_EPISODE_SCOPE_MUST_BE_NON_EMPTY
            )
        if not isinstance(self.episodes, tuple):
            raise InvalidModelError(
                SourceErrorMessages.SERIES_METADATA_EPISODE_SCOPE_MUST_BE_IMMUTABLE
            )
        if self.rights_status is not RightsStatus.ALLOWED:
            raise InvalidModelError(
                SourceErrorMessages.SERIES_METADATA_RIGHTS_MUST_BE_ALLOWED
            )
        if any(episode.series_id != self.series_id for episode in self.episodes):
            raise InvalidModelError(
                SourceErrorMessages.SERIES_METADATA_EPISODE_SCOPE_SERIES_MISMATCH
            )
        positions = tuple(
            (episode.position.season_number, episode.position.episode_number)
            for episode in self.episodes
        )
        if len(set(positions)) != len(positions):
            raise InvalidModelError(
                SourceErrorMessages.TVMAZE_DUPLICATE_REQUESTED_EPISODE
            )


@dataclass(frozen=True, slots=True)
class IngestSeriesMetadataResult:
    source_version: SourceVersion
    snapshot: SeriesMetadataSnapshot | None
    was_already_ingested: bool
