from uuid import UUID

import pytest
from langchain.agents.middleware import (
    LLMToolSelectorMiddleware,
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
)

from cinegraph.adapters.workflow.langgraph.agent_middleware import (
    build_agent_middleware,
)
from cinegraph.adapters.workflow.langgraph.runtime_context_integrity_middleware import (
    RuntimeContextIntegrityMiddleware,
)
from cinegraph.application.exceptions.errors import AgentRuntimeContextInvalidError
from cinegraph.common.error_messages import WorkflowErrorMessages
from cinegraph.common.prompts import TOOL_SELECTOR_SYSTEM_PROMPT
from tests.factories import make_episode_ref


class SimpleRuntime:
    # Store the context exposed to middleware hooks.
    def __init__(self, context: object) -> None:
        self.context = context


def valid_context() -> dict[str, object]:
    # Build the smallest valid invocation context for integrity validation.
    return {
        "episode": make_episode_ref(),
        "summary_source_document_id": UUID(int=2),
        "profile_watch_state": None,
    }


def test_runtime_context_integrity_accepts_valid_context() -> None:
    # Confirm valid context passes without changing state or context.
    context = valid_context()
    state = {"messages": []}

    result = RuntimeContextIntegrityMiddleware().before_agent(
        state, SimpleRuntime(context)
    )

    assert result is None
    assert context == valid_context()
    assert state == {"messages": []}


@pytest.mark.parametrize(
    "context",
    [
        None,
        {},
        {"episode": make_episode_ref()},
        {
            "episode": make_episode_ref(),
            "summary_source_document_id": "not-a-uuid",
            "profile_watch_state": None,
        },
        {
            "episode": make_episode_ref(),
            "summary_source_document_id": UUID(int=2),
            "profile_watch_state": "not-watch-state",
        },
    ],
)
def test_runtime_context_integrity_rejects_invalid_context(context: object) -> None:
    # Confirm every malformed context uses the centralized, value-safe error.
    with pytest.raises(AgentRuntimeContextInvalidError) as error:
        RuntimeContextIntegrityMiddleware().before_agent({}, SimpleRuntime(context))

    assert str(error.value) == WorkflowErrorMessages.AGENT_RUNTIME_CONTEXT_MUST_BE_VALID


def test_build_agent_middleware_returns_configured_stack() -> None:
    # Confirm production middleware remains in the documented outer-to-inner order.
    middleware = build_agent_middleware()

    assert [type(item) for item in middleware] == [
        RuntimeContextIntegrityMiddleware,
        ModelCallLimitMiddleware,
        ToolCallLimitMiddleware,
        ModelRetryMiddleware,
        LLMToolSelectorMiddleware,
    ]
    assert middleware[1].run_limit == 3
    assert middleware[1].exit_behavior == "end"
    assert middleware[2].tool_name == "grounded_episode_answer"
    assert middleware[2].run_limit == 1
    assert middleware[2].exit_behavior == "end"
    assert middleware[3].max_retries == 1
    assert middleware[3].retry_on == (ConnectionError, TimeoutError)
    assert middleware[3].on_failure == "error"
    assert middleware[3].initial_delay == 0.0
    assert middleware[3].jitter is False
    assert middleware[4].model is None
    assert middleware[4].max_tools == 1
    assert middleware[4].always_include == ["grounded_episode_answer"]
    assert middleware[4].system_prompt == TOOL_SELECTOR_SYSTEM_PROMPT
