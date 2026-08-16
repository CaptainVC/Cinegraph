from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class HttpAuditEvent:
    occurred_at: datetime
    request_id: str
    method: str
    path: str
    status_code: int
    duration_ms: float
    outcome: str
    principal_kind: str | None
