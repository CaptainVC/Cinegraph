from langchain.agents.middleware import (
    AgentMiddleware,
    LLMToolSelectorMiddleware,
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
)
from langchain_core.language_models import BaseChatModel

from cinegraph.adapters.workflow.langgraph.runtime_context_integrity_middleware import (
    RuntimeContextIntegrityMiddleware,
)
from cinegraph.common.prompts import TOOL_SELECTOR_SYSTEM_PROMPT


# Build the production middleware stack in outer-to-inner execution order.
def build_agent_middleware(
    tool_selector_model: BaseChatModel | None = None,
) -> tuple[AgentMiddleware, ...]:
    return (
        RuntimeContextIntegrityMiddleware(),
        ModelCallLimitMiddleware(run_limit=3, exit_behavior="end"),
        ToolCallLimitMiddleware(
            tool_name="grounded_episode_answer",
            run_limit=1,
            exit_behavior="end",
        ),
        ModelRetryMiddleware(
            max_retries=1,
            retry_on=(ConnectionError, TimeoutError),
            on_failure="error",
            initial_delay=0.0,
            jitter=False,
        ),
        LLMToolSelectorMiddleware(
            model=tool_selector_model,
            max_tools=1,
            always_include=["grounded_episode_answer"],
            system_prompt=TOOL_SELECTOR_SYSTEM_PROMPT,
        ),
    )