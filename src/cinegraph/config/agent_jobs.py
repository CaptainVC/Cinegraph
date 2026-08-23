from dataclasses import dataclass
from math import isfinite

from cinegraph.common.error_messages import AgentJobErrorMessages
from cinegraph.config.series_agent import DEFAULT_SERIES_AGENT_CONFIGURATION


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
    sse_max_events: int = 128
    sse_replay_batch: int = 64
    provider_timeout_seconds: float = 60.0
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
                self.provider_timeout_seconds,
            )
        ):
            raise ValueError(AgentJobErrorMessages.CONFIG_TIMING_LIMITS)
        if self.sse_replay_batch > self.sse_max_events:
            raise ValueError(AgentJobErrorMessages.CONFIG_REPLAY_LIMIT)
        if not (
            self.sse_poll_interval_seconds
            <= self.sse_heartbeat_interval_seconds
            <= self.sse_max_duration_seconds
        ):
            raise ValueError(AgentJobErrorMessages.CONFIG_TIMING_RELATION)
        if any(
            not value or value.strip() != value
            for value in (
                self.sse_media_type,
                self.sse_cache_control,
                self.sse_accel_buffering,
                self.sse_connection,
            )
        ):
            raise ValueError(AgentJobErrorMessages.CONFIG_SSE_HEADERS)


DEFAULT_AGENT_JOB_CONFIGURATION = AgentJobConfiguration()
