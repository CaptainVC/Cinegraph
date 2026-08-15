
from datetime import UTC, datetime


class SystemClock:

    # Return the current timezone-aware UTC timestamp.
    def now_utc(self) -> datetime:
        return datetime.now(UTC)
