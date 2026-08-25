"""Stable retry/error semantics shared by model middleware."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)

from cinegraph.application.models.agent_runtime import RuntimeFailureCode

TRANSIENT_MODEL_EXCEPTIONS: tuple[type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)


class AgentRuntimeBudgetExceeded(RuntimeError):
    code = RuntimeFailureCode.BUDGET_EXCEEDED


class AgentRuntimeFailure(RuntimeError):
    def __init__(self, code: RuntimeFailureCode):
        self.code = code
        super().__init__(code.value)


def classify_runtime_failure(exc: BaseException) -> RuntimeFailureCode | None:
    if isinstance(exc, (TimeoutError, APITimeoutError)):
        return RuntimeFailureCode.EXECUTION_TIMEOUT
    if isinstance(
        exc,
        (ConnectionError, APIConnectionError, InternalServerError, RateLimitError),
    ):
        return RuntimeFailureCode.PROVIDER_UNAVAILABLE
    return None


@dataclass(frozen=True, slots=True)
class RuntimeDeadline:
    ends_at: float
    clock: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        if (
            isinstance(self.ends_at, bool)
            or not isinstance(self.ends_at, (int, float))
            or not isfinite(self.ends_at)
            or self.ends_at <= 0
            or not callable(self.clock)
        ):
            raise ValueError("Runtime deadline configuration is invalid.")

    def remaining(self) -> float:
        return max(0.0, self.ends_at - self.clock())

    def check(self) -> None:
        if self.remaining() <= 0:
            raise AgentRuntimeFailure(RuntimeFailureCode.EXECUTION_TIMEOUT)
