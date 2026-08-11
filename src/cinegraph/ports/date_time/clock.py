from typing import Protocol
from datetime import datetime

class Clock(Protocol):
    # Processes the supplied now utc values.
    def now_utc(self) -> datetime: ...
