from typing import Protocol

from cinegraph.application.models.media_action import MediaActionAuditEvent


class MediaActionAuditSink(Protocol):
    def emit(self, event: MediaActionAuditEvent) -> None: ...
