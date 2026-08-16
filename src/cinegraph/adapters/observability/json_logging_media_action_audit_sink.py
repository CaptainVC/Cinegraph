import json
import logging
from dataclasses import asdict

from cinegraph.application.models.media_action import MediaActionAuditEvent


class JsonLoggingMediaActionAuditSink:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("cinegraph.media_actions")

    def emit(self, event: MediaActionAuditEvent) -> None:
        payload = asdict(event)
        payload["occurred_at"] = event.occurred_at.isoformat()
        for key, value in tuple(payload.items()):
            if hasattr(value, "value"):
                payload[key] = value.value
            elif hasattr(value, "hex"):
                payload[key] = str(value)
        self._logger.info(json.dumps(payload, sort_keys=True, separators=(",", ":")))
