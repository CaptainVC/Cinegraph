from functools import partial

from langchain.agents.middleware import (
    AgentMiddleware,
    LLMToolSelectorMiddleware,
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
    ToolErrorMiddleware,
)
from langchain_core.language_models import BaseChatModel

from cinegraph.adapters.workflow.langgraph.runtime_context_integrity_middleware import (
    RuntimeContextIntegrityMiddleware,
)
from cinegraph.common.prompts import TOOL_SELECTOR_SYSTEM_PROMPT
from cinegraph.common.error_messages import WorkflowErrorMessages
from cinegraph.config.agent_middleware import (
    AgentMiddlewareConfiguration,
    DEFAULT_AGENT_MIDDLEWARE_CONFIGURATION,
)


# Return a sanitized result only for the configured grounded answer tool.
def _handle_tool_error(
    exception: Exception, request: object, grounded_tool_name: str
) -> str | None:
    tool_call = getattr(request, "tool_call", {})
    if tool_call.get("name") == grounded_tool_name:
        return WorkflowErrorMessages.GROUNDED_ANSWER_TOOL_UNAVAILABLE
    return None


# Build the production middleware stack in outer-to-inner execution order.
def build_agent_middleware(
    tool_selector_model: BaseChatModel | None = None,
    configuration: AgentMiddlewareConfiguration = DEFAULT_AGENT_MIDDLEWARE_CONFIGURATION,
) -> tuple[AgentMiddleware, ...]:
    return (
        RuntimeContextIntegrityMiddleware(),
        ModelCallLimitMiddleware(
            run_limit=configuration.model_run_limit, exit_behavior="end"
        ),
        ToolCallLimitMiddleware(
            tool_name=configuration.grounded_tool_name,
            run_limit=configuration.grounded_tool_run_limit,
            exit_behavior="end",
        ),
        ToolErrorMiddleware(
            on_error=partial(
                _handle_tool_error,
                grounded_tool_name=configuration.grounded_tool_name,
            ),
            tools=configuration.tool_error_tool_names,
        ),
        ModelRetryMiddleware(
            max_retries=configuration.model_retry_count,
            retry_on=(ConnectionError, TimeoutError),
            on_failure="error",
            initial_delay=configuration.retry_initial_delay,
            jitter=configuration.retry_jitter,
        ),
        LLMToolSelectorMiddleware(
            model=tool_selector_model,
            max_tools=configuration.selector_max_tools,
            always_include=list(configuration.selector_always_included_names),
            system_prompt=TOOL_SELECTOR_SYSTEM_PROMPT,
        ),
    )
