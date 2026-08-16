from uuid import UUID

import pytest
from langchain.agents.middleware import (
    LLMToolSelectorMiddleware,
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
    ToolErrorMiddleware,
)

from cinegraph.adapters.workflow.langgraph.agent_middleware import (
    _handle_tool_error,
    build_agent_middleware,
)
from cinegraph.adapters.workflow.langgraph.runtime_context_integrity_middleware import (
    RuntimeContextIntegrityMiddleware,
)
from cinegraph.application.exceptions.errors import AgentRuntimeContextInvalidError
from cinegraph.common.error_messages import WorkflowErrorMessages
from cinegraph.common.prompts import TOOL_SELECTOR_SYSTEM_PROMPT
from cinegraph.config.agent_middleware import (
    DEFAULT_AGENT_MIDDLEWARE_CONFIGURATION,
    AgentMiddlewareConfiguration,
)
from tests.factories import (
    make_authenticated_corpus_access_scope,
    make_episode_ref,
    make_guest_corpus_access_scope,
)


class SimpleRuntime:
    # Store the context exposed to middleware hooks.
    def __init__(self, context: object) -> None:
        self.context = context


class SimpleToolRequest:
    # Expose only the tool-call field needed by the private handler.
    def __init__(self, tool_name: str) -> None:
        self.tool_call = {"name": tool_name}


def valid_context() -> dict[str, object]:
    # Build the smallest valid invocation context for integrity validation.
    return {
        "episode": make_episode_ref(),
        "summary_source_document_id": UUID(int=2),
        "profile_watch_state": None,
        "corpus_access_scope": make_authenticated_corpus_access_scope(),
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
            "corpus_access_scope": make_authenticated_corpus_access_scope(),
        },
        {
            "episode": make_episode_ref(),
            "summary_source_document_id": UUID(int=2),
            "profile_watch_state": "not-watch-state",
            "corpus_access_scope": make_authenticated_corpus_access_scope(),
        },
        {
            "episode": make_episode_ref(),
            "summary_source_document_id": UUID(int=2),
            "profile_watch_state": None,
            "corpus_access_scope": "not-an-access-scope",
        },
        {
            "episode": make_episode_ref(season_number=3),
            "summary_source_document_id": UUID(int=2),
            "profile_watch_state": None,
            "corpus_access_scope": make_guest_corpus_access_scope(),
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
        ToolErrorMiddleware,
        ModelRetryMiddleware,
        LLMToolSelectorMiddleware,
    ]
    assert middleware[1].run_limit == DEFAULT_AGENT_MIDDLEWARE_CONFIGURATION.model_run_limit
    assert middleware[1].exit_behavior == "end"
    assert middleware[2].tool_name == DEFAULT_AGENT_MIDDLEWARE_CONFIGURATION.grounded_tool_name
    assert middleware[2].run_limit == DEFAULT_AGENT_MIDDLEWARE_CONFIGURATION.grounded_tool_run_limit
    assert middleware[2].exit_behavior == "end"
    assert middleware[3].on_error.func is _handle_tool_error
    assert middleware[3]._tool_filter == [
        *DEFAULT_AGENT_MIDDLEWARE_CONFIGURATION.tool_error_tool_names
    ]
    assert middleware[4].max_retries == DEFAULT_AGENT_MIDDLEWARE_CONFIGURATION.model_retry_count
    assert middleware[4].retry_on == (ConnectionError, TimeoutError)
    assert middleware[4].on_failure == "error"
    assert middleware[4].initial_delay == DEFAULT_AGENT_MIDDLEWARE_CONFIGURATION.retry_initial_delay
    assert middleware[4].jitter is DEFAULT_AGENT_MIDDLEWARE_CONFIGURATION.retry_jitter
    assert middleware[5].model is None
    assert middleware[5].max_tools == DEFAULT_AGENT_MIDDLEWARE_CONFIGURATION.selector_max_tools
    assert middleware[5].always_include == [
        *DEFAULT_AGENT_MIDDLEWARE_CONFIGURATION.selector_always_included_names
    ]
    assert middleware[5].system_prompt == TOOL_SELECTOR_SYSTEM_PROMPT


def test_build_agent_middleware_uses_custom_configuration() -> None:
    # Confirm every configurable tuning value reaches its middleware.
    configuration = AgentMiddlewareConfiguration(
        model_run_limit=7,
        grounded_tool_run_limit=2,
        grounded_tool_name="custom_grounded_tool",
        model_retry_count=4,
        retry_initial_delay=0.5,
        retry_jitter=True,
        selector_max_tools=3,
        selector_always_included_names=("custom_grounded_tool",),
        tool_error_tool_names=("custom_grounded_tool",),
    )

    middleware = build_agent_middleware(configuration=configuration)

    assert middleware[1].run_limit == 7
    assert middleware[2].tool_name == "custom_grounded_tool"
    assert middleware[2].run_limit == 2
    assert middleware[3]._tool_filter == ["custom_grounded_tool"]
    assert middleware[4].max_retries == 4
    assert middleware[4].initial_delay == 0.5
    assert middleware[4].jitter is True
    assert middleware[5].max_tools == 3
    assert middleware[5].always_include == ["custom_grounded_tool"]


def test_handle_tool_error_sanitizes_grounded_tool_failure() -> None:
    # Confirm grounded tool failures expose only the centralized safe message.
    result = _handle_tool_error(
        RuntimeError("secret detail"),
        SimpleToolRequest("grounded_episode_answer"),
        "grounded_episode_answer",
    )

    assert result == WorkflowErrorMessages.GROUNDED_ANSWER_TOOL_UNAVAILABLE
    assert "secret detail" not in result


def test_handle_tool_error_propagates_unknown_tool_failure() -> None:
    # Confirm unknown tool failures remain unhandled.
    assert (
        _handle_tool_error(
            RuntimeError("secret detail"),
            SimpleToolRequest("unknown_tool"),
            "grounded_episode_answer",
        )
        is None
    )
