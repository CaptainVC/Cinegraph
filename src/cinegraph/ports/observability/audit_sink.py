from typing import Protocol

from cinegraph.application.models.audit import HttpAuditEvent, RuntimeTelemetryEvent


class AuditSink(Protocol):
    def emit(self, event: HttpAuditEvent) -> None: ...


class RuntimeTelemetrySink(Protocol):
    def emit(self, event: RuntimeTelemetryEvent) -> None: ...


class FailureIsolatingTelemetrySink:
    """Best-effort sink boundary: telemetry outages cannot affect product behavior."""

    def __init__(self, sink: RuntimeTelemetrySink) -> None:
        self._sink = sink

    def emit(self, event: RuntimeTelemetryEvent) -> None:
        try:
            self._sink.emit(event)
        except Exception:
            # Deliberately do not log the exception: its text may contain secrets.
            return
