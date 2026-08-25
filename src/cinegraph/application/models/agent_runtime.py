"""Stable public runtime failure taxonomy for agent jobs and telemetry."""

from enum import StrEnum

from cinegraph.common.error_messages import AgentJobErrorMessages


class RuntimeFailureCode(StrEnum):
    EXECUTION_TIMEOUT = "execution_timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    BUDGET_EXCEEDED = "budget_exceeded"
    EXECUTION_FAILED = AgentJobErrorMessages.EXECUTION_FAILED


ALLOWED_AGENT_JOB_FAILURE_CODES = frozenset(
    {*(code.value for code in RuntimeFailureCode), AgentJobErrorMessages.DISPATCH_UNAVAILABLE}
)
