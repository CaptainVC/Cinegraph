from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    # Return the current timezone-aware UTC timestamp.
    def now_utc(self) -> datetime: ...
