import json
import logging
from dataclasses import asdict

from cinegraph.application.models.audit import HttpAuditEvent


class JsonLoggingAuditSink:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("cinegraph.audit")

    def emit(self, event: HttpAuditEvent) -> None:
        payload = asdict(event)
        payload["occurred_at"] = event.occurred_at.isoformat()
        self._logger.info(json.dumps(payload, sort_keys=True, separators=(",", ":")))
