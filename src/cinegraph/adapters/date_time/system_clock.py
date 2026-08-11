
from datetime import UTC, datetime


class SystemClock:

    # Processes the supplied now utc values.
    def now_utc(self) -> datetime:
        return datetime.now(UTC)
