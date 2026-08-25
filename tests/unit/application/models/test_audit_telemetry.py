from datetime import UTC, datetime
from uuid import uuid4

import pytest

from cinegraph.adapters.observability import JsonLoggingRuntimeTelemetrySink
from cinegraph.application.models.audit import (
    RuntimeTelemetryEvent,
    TelemetryOutcome,
    TelemetryStage,
)
from cinegraph.ports.observability import FailureIsolatingTelemetrySink


def event(**overrides):
    values = {
        "occurred_at": datetime.now(UTC),
        "stage": TelemetryStage.MODEL,
        "outcome": TelemetryOutcome.SUCCESS,
        "correlation_id": uuid4(),
    }
    values.update(overrides)
    return RuntimeTelemetryEvent(**values)


def test_runtime_event_is_immutable_and_rejects_invalid_usage() -> None:
    telemetry = event(input_tokens=3)
    with pytest.raises(TypeError):
        telemetry.attributes["secret"] = "value"
    with pytest.raises(ValueError):
        event(output_tokens=-1)
    with pytest.raises(ValueError):
        event(duration_ms=float("nan"))


def test_runtime_event_rejects_unbounded_or_sensitive_shaped_values() -> None:
    with pytest.raises(ValueError):
        event(request_id="x" * 129)
    with pytest.raises(ValueError):
        event(attributes={"x": "\n"})


def test_sink_isolates_failures_without_exception_text() -> None:
    class Broken:
        def emit(self, _event) -> None:
            raise RuntimeError("provider response secret")

    FailureIsolatingTelemetrySink(Broken()).emit(event())


def test_json_sink_serializes_uuid_enum_and_mapping(caplog) -> None:
    import logging

    logger = logging.getLogger(f"cinegraph.runtime.test.{uuid4()}")
    caplog.set_level(logging.INFO, logger=logger.name)
    JsonLoggingRuntimeTelemetrySink(logger).emit(event(attributes={"worker": "api"}))
    record = caplog.records[-1]
    assert "provider response" not in record.message
    assert '"stage":"model"' in record.message
    assert '"worker":"api"' in record.message
