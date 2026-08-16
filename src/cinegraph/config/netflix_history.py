from dataclasses import dataclass
from datetime import timedelta

from cinegraph.common.error_messages import NetflixHistoryErrorMessages


NETFLIX_HISTORY_TITLE_COLUMN = "Title"
NETFLIX_HISTORY_DATE_COLUMN = "Date"
NETFLIX_HISTORY_REQUIRED_COLUMNS = (
    NETFLIX_HISTORY_TITLE_COLUMN,
    NETFLIX_HISTORY_DATE_COLUMN,
)
NETFLIX_HISTORY_FORMULA_PREFIXES = ("=", "+", "-", "@")
NETFLIX_HISTORY_DATE_FORMATS = ("%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d")
NETFLIX_SEASON_EPISODE_TITLE_PATTERN = (
    r"^(?P<series>.+?):\s*S(?P<season>\d+):E(?P<episode>\d+):\s*(?P<title>.+)$"
)
NETFLIX_LONG_EPISODE_PATTERN = (
    r"^(?P<series>.+?):\s*Season\s+(?P<season>\d+):\s*"
    r"Episode\s+(?P<episode>\d+)(?::\s*(?P<title>.+))?$"
)
NETFLIX_SEASON_TITLE_PATTERN = (
    r"^(?P<series>.+?):\s*Season\s+(?P<season>\d+):\s*(?P<title>.+)$"
)
NETFLIX_SERIES_TITLE_PATTERN = r"^(?P<series>.+?):\s*(?P<title>.+)$"


@dataclass(frozen=True, slots=True)
class NetflixHistoryImportConfiguration:
    maximum_upload_bytes: int
    maximum_rows: int
    maximum_title_characters: int
    accepted_content_types: frozenset[str]
    pending_review_retention: timedelta

    def __post_init__(self) -> None:
        if (
            self.maximum_upload_bytes < 1
            or self.maximum_rows < 1
            or self.maximum_title_characters < 1
            or not self.accepted_content_types
            or self.pending_review_retention <= timedelta(0)
        ):
            raise ValueError(NetflixHistoryErrorMessages.CONFIGURATION_INVALID)


DEFAULT_NETFLIX_HISTORY_IMPORT_CONFIGURATION = NetflixHistoryImportConfiguration(
    maximum_upload_bytes=5 * 1024 * 1024,
    maximum_rows=50_000,
    maximum_title_characters=500,
    accepted_content_types=frozenset(
        {
            "text/csv",
            "application/csv",
            "application/vnd.ms-excel",
        }
    ),
    pending_review_retention=timedelta(days=7),
)
