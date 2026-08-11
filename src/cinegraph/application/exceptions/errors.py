
from uuid import UUID

from cinegraph.common.error_messages import SourceErrorMessages, WatchErrorMessages


class ProfileWatchStateNotFoundError(LookupError):

    # Initializes the object with its required state.
    def __init__(self, profile_id: UUID) -> None:
        super().__init__(
            WatchErrorMessages
            .NO_WATCH_STATE_FOUND_FOR_PROFILE_ID
            .format(profile_id=profile_id)
        )


class SeasonNotFoundError(LookupError):
    # Initializes the object with its required state.
    def __init__(self, series_id: UUID, season_id: UUID) -> None:
        super().__init__(
            WatchErrorMessages.NO_SEASON_FOUND_FOR_SERIES_ID.format(
                series_id=series_id,
                season_id=season_id,
            )
        )

class SourceVersionNotFoundError(LookupError):
    # Initializes the object with its required state.
    def __init__(self, source_version_id: UUID) -> None:
        super().__init__(
            SourceErrorMessages.SOURCE_VERSION_NOT_FOUND.format(
                source_version_id=source_version_id,
            )
        )
