from typing import Protocol
from datetime import datetime

class Clock(Protocol):
    def now_utc(self) -> datetime: ...
