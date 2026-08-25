import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping
from uuid import UUID

from cinegraph.application.models.agent_runtime import ALLOWED_AGENT_JOB_FAILURE_CODES
from cinegraph.config import DEFAULT_OBSERVABILITY_CONFIGURATION

_ALLOWED_ATTRIBUTE_KEYS = frozenset({"worker", "dependency", "retry_class"})
_LOW_CARDINALITY = frozenset(
    {"api", "database", "qdrant", "openai", "none", "transient", "permanent"}
)
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class TelemetryStage(StrEnum):
    HTTP = "http"
    QUEUED = "queued"
    RUNNING = "running"
    TERMINAL = "terminal"
    MODEL = "model"


class TelemetryOutcome(StrEnum):
    SUCCESS = "success"
    CLIENT_ERROR = "client_error"
    SERVER_ERROR = "server_error"
    FAILURE = "failure"


def _validate_opaque(value: str, field: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError(f"{field} must be a bounded opaque string")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{field} contains control characters")


def _validate_duration(value: float, field: str = "duration_ms") -> None:
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{field} must be finite and nonnegative")


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

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is not UTC:
            raise ValueError("occurred_at must be UTC")
        _validate_opaque(self.request_id, "request_id")
        _validate_opaque(self.method, "method")
        _validate_opaque(self.path, "path")
        if self.method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}:
            raise ValueError("method is not an allowed HTTP method")
        if "?" in self.path or "#" in self.path:
            raise ValueError("path must not contain query or fragment")
        _validate_duration(self.duration_ms)
        if not 100 <= self.status_code <= 599:
            raise ValueError("status_code is outside HTTP range")
        if self.principal_kind is not None:
            _validate_opaque(self.principal_kind, "principal_kind")
            if self.principal_kind not in {"guest", "authenticated", "anonymous"}:
                raise ValueError("principal_kind is not an allowed dimension")
        if self.outcome not in {"success", "client_error", "server_error"}:
            raise ValueError("outcome is not an allowed dimension")


@dataclass(frozen=True, slots=True)
class RuntimeTelemetryEvent:
    """Privacy-safe aggregate runtime event. Never add user/content fields here."""

    occurred_at: datetime
    stage: TelemetryStage
    outcome: TelemetryOutcome
    correlation_id: UUID
    request_id: str | None = None
    job_id: UUID | None = None
    duration_ms: float | None = None
    failure_code: str | None = None
    model_role: str | None = None
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_micros: int | None = None
    citation_count: int = 0
    attributes: Mapping[str, str] = MappingProxyType({})

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is not UTC:
            raise ValueError("occurred_at must be UTC")
        if self.occurred_at.astimezone(UTC).year < 2000:
            raise ValueError("occurred_at is outside supported range")
        if self.request_id is not None:
            _validate_opaque(self.request_id, "request_id")
            if _REQUEST_ID.fullmatch(self.request_id) is None:
                raise ValueError("request_id is not sanitized")
        if not isinstance(self.correlation_id, UUID):
            raise ValueError("correlation_id must be a UUID")
        if self.job_id is not None and not isinstance(self.job_id, UUID):
            raise ValueError("job_id must be a UUID")
        if not isinstance(self.stage, TelemetryStage) or not isinstance(
            self.outcome, TelemetryOutcome
        ):
            raise ValueError("stage and outcome must be telemetry enums")
        if self.duration_ms is not None:
            _validate_duration(self.duration_ms)
            if self.duration_ms > DEFAULT_OBSERVABILITY_CONFIGURATION.maximum_duration_ms:
                raise ValueError("duration_ms exceeds configured maximum")
        for field in (
            "model_calls",
            "tool_calls",
            "input_tokens",
            "output_tokens",
            "citation_count",
        ):
            value = getattr(self, field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{field} must be a nonnegative integer")
        if self.estimated_cost_micros is not None and (
            not isinstance(self.estimated_cost_micros, int)
            or isinstance(self.estimated_cost_micros, bool)
            or self.estimated_cost_micros < 0
        ):
            raise ValueError("estimated_cost_micros must be a nonnegative integer")
        if self.failure_code is not None:
            _validate_opaque(self.failure_code, "failure_code")
            if self.failure_code not in ALLOWED_AGENT_JOB_FAILURE_CODES:
                raise ValueError("failure_code is not allowed")
        if self.model_role is not None:
            _validate_opaque(self.model_role, "model_role")
            if self.model_role != "aggregate":
                raise ValueError("model_role is not allowed")
        clean = dict(self.attributes)
        if len(clean) > DEFAULT_OBSERVABILITY_CONFIGURATION.maximum_attribute_count:
            raise ValueError("attributes are too numerous")
        for key, value in clean.items():
            if key not in _ALLOWED_ATTRIBUTE_KEYS or value not in _LOW_CARDINALITY:
                raise ValueError("attribute is not an allowed low-cardinality dimension")
        object.__setattr__(self, "attributes", MappingProxyType(clean))
