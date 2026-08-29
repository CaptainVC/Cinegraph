import pytest

from cinegraph.config import (
    DEFAULT_AGENT_JOB_CONFIGURATION,
    DEFAULT_AGENT_RUNTIME_CONTROLS,
    AgentJobConfiguration,
    agent_client_job_deadline_ms,
    agent_client_poll_interval_ms,
)


def test_client_timing_is_integer_and_covers_server_runtime() -> None:
    poll_interval = agent_client_poll_interval_ms()
    deadline = agent_client_job_deadline_ms(
        max_execution_duration_seconds=(
            DEFAULT_AGENT_RUNTIME_CONTROLS.max_execution_duration_seconds
        ),
    )

    assert isinstance(poll_interval, int)
    assert isinstance(deadline, int)
    assert poll_interval > 0
    assert (
        deadline
        >= (
            max(
                DEFAULT_AGENT_JOB_CONFIGURATION.sse_max_duration_seconds,
                DEFAULT_AGENT_RUNTIME_CONTROLS.max_execution_duration_seconds,
            )
            + DEFAULT_AGENT_JOB_CONFIGURATION.transport_grace_seconds
        )
        * 1_000
    )


def test_client_timing_rejects_invalid_transport_grace() -> None:
    with pytest.raises(ValueError):
        AgentJobConfiguration(transport_grace_seconds=0)


def test_client_deadline_rejects_invalid_execution_duration() -> None:
    with pytest.raises(ValueError):
        agent_client_job_deadline_ms(max_execution_duration_seconds=0)
