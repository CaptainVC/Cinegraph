from dataclasses import dataclass
from math import ceil, isfinite

from cinegraph.common.error_messages import AgentJobErrorMessages
from cinegraph.config.agent_runtime_controls import DEFAULT_AGENT_RUNTIME_CONTROLS
from cinegraph.config.series_agent import DEFAULT_SERIES_AGENT_CONFIGURATION

AGENT_JOB_SUPERVISOR_ADVISORY_LOCK_KEY = 0x43494E4547524150


@dataclass(frozen=True, slots=True)
class AgentJobConfiguration:
    question_min_length: int = 2
    question_max_length: int = 2_000
    candidate_max_episodes: int = DEFAULT_SERIES_AGENT_CONFIGURATION.max_candidate_episodes
    idempotency_key_max_length: int = 36
    worker_limit: int = 4
    pending_limit: int = 32
    sse_poll_interval_seconds: float = 0.05
    sse_heartbeat_interval_seconds: float = 15.0
    sse_max_duration_seconds: float = 120.0
    # Browser fallback polling is deliberately slower than the worker's SSE
    # replay loop.  Keep it here so the browser cannot drift from server
    # behavior by carrying an independent timeout constant.
    client_poll_interval_seconds: float = 1.2
    transport_grace_seconds: int = 15
    recovery_scan_interval_seconds: float = 5.0
    recovery_batch_size: int = 32
    recovery_shutdown_timeout_seconds: float = 5.0
    sse_max_events: int = 128
    sse_replay_batch: int = 64
    provider_timeout_seconds: float = 60.0
    evidence_citation_limit: int = 32
    evidence_text_max_chars: int = 4_000
    evidence_cache_control: str = "private, no-store"
    sse_media_type: str = "text/event-stream"
    sse_cache_control: str = "no-cache, no-transform"
    sse_accel_buffering: str = "no"
    sse_connection: str = "keep-alive"

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in (
                self.question_min_length,
                self.question_max_length,
                self.candidate_max_episodes,
                self.idempotency_key_max_length,
                self.worker_limit,
                self.pending_limit,
                self.sse_max_events,
                self.sse_replay_batch,
                self.evidence_citation_limit,
                self.evidence_text_max_chars,
                self.transport_grace_seconds,
                self.recovery_batch_size,
            )
        ):
            raise ValueError(AgentJobErrorMessages.CONFIG_INTEGER_LIMITS)
        if self.idempotency_key_max_length != 36:
            raise ValueError(AgentJobErrorMessages.CONFIG_IDEMPOTENCY_UUID)
        if self.question_min_length > self.question_max_length:
            raise ValueError(AgentJobErrorMessages.CONFIG_QUESTION_LIMITS)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            or value <= 0
            for value in (
                self.sse_poll_interval_seconds,
                self.sse_heartbeat_interval_seconds,
                self.sse_max_duration_seconds,
                self.client_poll_interval_seconds,
                self.recovery_scan_interval_seconds,
                self.recovery_shutdown_timeout_seconds,
                self.provider_timeout_seconds,
            )
        ):
            raise ValueError(AgentJobErrorMessages.CONFIG_TIMING_LIMITS)
        if self.sse_replay_batch > self.sse_max_events:
            raise ValueError(AgentJobErrorMessages.CONFIG_REPLAY_LIMIT)
        if self.recovery_batch_size > self.pending_limit:
            raise ValueError(AgentJobErrorMessages.CONFIG_RECOVERY_BATCH)
        if not (
            self.sse_poll_interval_seconds
            <= self.sse_heartbeat_interval_seconds
            <= self.sse_max_duration_seconds
        ):
            raise ValueError(AgentJobErrorMessages.CONFIG_TIMING_RELATION)
        if self.client_poll_interval_seconds * 1_000 < 1:
            raise ValueError(AgentJobErrorMessages.CONFIG_TIMING_LIMITS)
        if any(
            not value or value.strip() != value
            for value in (
                self.sse_media_type,
                self.sse_cache_control,
                self.sse_accel_buffering,
                self.sse_connection,
                self.evidence_cache_control,
            )
        ):
            raise ValueError(AgentJobErrorMessages.CONFIG_SSE_HEADERS)


DEFAULT_AGENT_JOB_CONFIGURATION = AgentJobConfiguration()


def agent_client_poll_interval_ms(
    configuration: AgentJobConfiguration = DEFAULT_AGENT_JOB_CONFIGURATION,
) -> int:
    """Return the validated browser fallback polling interval in milliseconds."""

    return ceil(configuration.client_poll_interval_seconds * 1_000)


def agent_client_job_deadline_ms(
    configuration: AgentJobConfiguration = DEFAULT_AGENT_JOB_CONFIGURATION,
    *,
    max_execution_duration_seconds: int = (
        DEFAULT_AGENT_RUNTIME_CONTROLS.max_execution_duration_seconds
    ),
) -> int:
    """Return a browser deadline covering execution, SSE, and transport grace."""

    if (
        isinstance(max_execution_duration_seconds, bool)
        or not isinstance(max_execution_duration_seconds, int)
        or max_execution_duration_seconds <= 0
    ):
        raise ValueError(AgentJobErrorMessages.CONFIG_EXECUTION_DURATION)
    duration_seconds = max(
        configuration.sse_max_duration_seconds,
        max_execution_duration_seconds,
    ) + configuration.transport_grace_seconds
    return ceil(duration_seconds * 1_000)
