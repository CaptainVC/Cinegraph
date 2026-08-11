from typing import Protocol
from datetime import datetime

class Clock(Protocol):
    # Return the current timezone-aware UTC timestamp.
    def now_utc(self) -> datetime: ...
