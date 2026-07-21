
from uuid import UUID

from cinegraph.common.error_messages import WatchErrorMessages


class ProfileWatchStateNotFoundError(LookupError):

    def __init__(self, profile_id: UUID) -> None:
        super().__init__(
            WatchErrorMessages
            .NO_WATCH_STATE_FOUND_FOR_PROFILE_ID
            .format(profile_id=profile_id)
        )


class SeasonNotFoundError(LookupError):
    def __init__(self, series_id: UUID, season_id: UUID) -> None:
        super().__init__(
            WatchErrorMessages.NO_SEASON_FOUND_FOR_SERIES_ID.format(
                series_id=series_id,
                season_id=season_id,
            )
        )