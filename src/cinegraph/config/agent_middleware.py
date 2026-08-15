from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentMiddlewareConfiguration:
    model_run_limit: int
    grounded_tool_run_limit: int
    grounded_tool_name: str
    model_retry_count: int
    retry_initial_delay: float
    retry_jitter: bool
    selector_max_tools: int
    selector_always_included_names: tuple[str, ...]
    tool_error_tool_names: tuple[str, ...]


DEFAULT_AGENT_MIDDLEWARE_CONFIGURATION = AgentMiddlewareConfiguration(
    model_run_limit=3,
    grounded_tool_run_limit=1,
    grounded_tool_name="grounded_episode_answer",
    model_retry_count=1,
    retry_initial_delay=0.0,
    retry_jitter=False,
    selector_max_tools=1,
    selector_always_included_names=("grounded_episode_answer",),
    tool_error_tool_names=("grounded_episode_answer",),
)
