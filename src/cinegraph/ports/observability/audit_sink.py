from typing import Protocol

from cinegraph.application.models.audit import HttpAuditEvent


class AuditSink(Protocol):
    def emit(self, event: HttpAuditEvent) -> None: ...
