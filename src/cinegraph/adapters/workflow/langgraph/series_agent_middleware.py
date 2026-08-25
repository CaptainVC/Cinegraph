from typing import cast

from langchain.agents.middleware import (
    AgentMiddleware,
    LLMToolSelectorMiddleware,
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
    ToolErrorMiddleware,
)
from langchain_core.language_models import BaseChatModel

from cinegraph.adapters.workflow.langgraph.series_runtime_context_integrity_middleware import (
    SeriesRuntimeContextIntegrityMiddleware,
)
from cinegraph.application.service.agent_runtime_resilience import (
    TRANSIENT_MODEL_EXCEPTIONS,
)
from cinegraph.common.error_messages import WorkflowErrorMessages
from cinegraph.common.prompts import SERIES_TOOL_SELECTOR_SYSTEM_PROMPT
from cinegraph.config.series_agent import (
    DEFAULT_SERIES_AGENT_CONFIGURATION,
    SERIES_GRAPH_TOOL_NAME,
    SERIES_TRANSCRIPT_TOOL_NAME,
    SeriesAgentConfiguration,
)


def _handle_series_tool_error(exception: Exception, request: object) -> str:
    del exception, request
    return WorkflowErrorMessages.SERIES_AGENT_TOOL_UNAVAILABLE


def build_series_agent_middleware(
    tool_selector_model: BaseChatModel | None = None,
    configuration: SeriesAgentConfiguration = DEFAULT_SERIES_AGENT_CONFIGURATION,
) -> tuple[AgentMiddleware, ...]:
    middleware = (
        SeriesRuntimeContextIntegrityMiddleware(configuration),
        ModelCallLimitMiddleware(run_limit=configuration.model_call_limit, exit_behavior="end"),
        ToolCallLimitMiddleware(
            tool_name=SERIES_TRANSCRIPT_TOOL_NAME,
            run_limit=configuration.transcript_tool_call_limit,
            exit_behavior="end",
        ),
        ToolCallLimitMiddleware(
            tool_name=SERIES_GRAPH_TOOL_NAME,
            run_limit=configuration.graph_tool_call_limit,
            exit_behavior="end",
        ),
        ToolCallLimitMiddleware(
            tool_name=None, run_limit=configuration.total_tool_call_limit, exit_behavior="end"
        ),
        ToolErrorMiddleware(
            on_error=_handle_series_tool_error,
            tools=[SERIES_TRANSCRIPT_TOOL_NAME, SERIES_GRAPH_TOOL_NAME],
        ),
        ModelRetryMiddleware(
            max_retries=configuration.model_retry_count,
            retry_on=TRANSIENT_MODEL_EXCEPTIONS,
            on_failure="error",
            initial_delay=configuration.retry_initial_delay,
            jitter=configuration.retry_jitter,
        ),
        LLMToolSelectorMiddleware(
            model=tool_selector_model,
            max_tools=configuration.tool_selector_max_tools,
            always_include=[SERIES_TRANSCRIPT_TOOL_NAME, SERIES_GRAPH_TOOL_NAME],
            system_prompt=SERIES_TOOL_SELECTOR_SYSTEM_PROMPT,
        ),
    )
    return cast(tuple[AgentMiddleware, ...], middleware)
