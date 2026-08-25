import json
import logging
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from uuid import UUID

from cinegraph.application.models.audit import HttpAuditEvent, RuntimeTelemetryEvent


def _json_default(value: object) -> object:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Enum, UUID)):
        return str(value.value if isinstance(value, Enum) else value)
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError("unsupported telemetry value")


def _emit_json(logger: logging.Logger, event: HttpAuditEvent | RuntimeTelemetryEvent) -> None:
    payload = (
        {field.name: getattr(event, field.name) for field in fields(event)}
        if is_dataclass(event)
        else event
    )
    logger.info(json.dumps(payload, default=_json_default, sort_keys=True, separators=(",", ":")))


class JsonLoggingAuditSink:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("cinegraph.audit")

    def emit(self, event: HttpAuditEvent) -> None:
        _emit_json(self._logger, event)


class JsonLoggingRuntimeTelemetrySink:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("cinegraph.runtime")

    def emit(self, event: RuntimeTelemetryEvent) -> None:
        _emit_json(self._logger, event)
