from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from cinegraph.application.models.model_usage import (
    AgentUsageBudget,
    LangChainUsageCallback,
    ModelUsageLedger,
    UsageMetadataError,
    current_usage_budget,
)
from cinegraph.application.service.agent_runtime_resilience import (
    AgentRuntimeBudgetExceeded,
    AgentRuntimeFailure,
    RuntimeDeadline,
    RuntimeFailureCode,
    classify_runtime_failure,
)
from cinegraph.config.agent_runtime_controls import AgentRuntimeControlConfiguration, ModelTokenRate


def controls(**changes: object) -> AgentRuntimeControlConfiguration:
    values: dict[str, object] = {
        "max_model_calls": 8,
        "max_input_tokens": 100,
        "max_output_tokens": 100,
        "max_total_tokens": 200,
        "max_estimated_cost_micros": 10_000,
        "rates_by_model": {
            "terra": ModelTokenRate(1_000_000, 500_000, 2_000_000),
            "luna": ModelTokenRate(1_000_000, 500_000, 2_000_000),
        },
    }
    values.update(changes)
    return AgentRuntimeControlConfiguration(**values)


def result(identity: str, prompt: int = 3, completion: int = 2) -> LLMResult:
    message = AIMessage(
        content="must never be inspected",
        id=identity,
        usage_metadata={
            "input_tokens": prompt,
            "output_tokens": completion,
            "total_tokens": prompt + completion,
        },
    )
    return LLMResult(generations=[[ChatGeneration(message=message)]])


def test_callback_is_inactive_outside_scope() -> None:
    callback = LangChainUsageCallback("terra", "terra")
    callback.on_chat_model_start({}, [], run_id=uuid4())
    callback.on_llm_end(result("x"))
    assert current_usage_budget() is None


def test_terra_and_nested_luna_aggregate_and_reset() -> None:
    budget = AgentUsageBudget(controls())
    terra = LangChainUsageCallback("synthesis", "terra")
    luna = LangChainUsageCallback("grounded_answer", "luna")
    with budget.scope():
        terra.on_chat_model_start({}, [], run_id="a")
        terra.on_llm_end(result("terra-1"))
        luna.on_chat_model_start({}, [], run_id="b")
        luna.on_llm_end(result("luna-1"))
    assert budget.ledger.total_tokens == 10
    assert {entry.model_role for entry in budget.ledger.entries} == {"synthesis", "grounded_answer"}
    assert current_usage_budget() is None


def test_start_limit_and_budget_exception_propagate() -> None:
    budget = AgentUsageBudget(controls(max_model_calls=1))
    callback = LangChainUsageCallback("terra", "terra")
    with budget.scope():
        callback.on_chat_model_start({}, [], run_id="one")
        callback.on_llm_end(result("one"))
        with pytest.raises(AgentRuntimeBudgetExceeded):
            callback.on_chat_model_start({}, [], run_id="two")


def test_strict_missing_usage_duplicate_and_conflicting_id() -> None:
    budget = AgentUsageBudget(controls())
    callback = LangChainUsageCallback("terra", "terra")
    missing = LLMResult(
        generations=[[ChatGeneration(message=AIMessage(content="x", id="missing"))]]
    )
    with budget.scope():
        with pytest.raises(UsageMetadataError):
            callback.on_llm_end(missing)
        callback.on_llm_end(result("same"))
        callback.on_llm_end(result("same"))
        with pytest.raises(UsageMetadataError):
            callback.on_llm_end(result("same", prompt=4))


def test_concurrent_scopes_are_isolated() -> None:
    def run(identity: str) -> int:
        budget = AgentUsageBudget(controls())
        callback = LangChainUsageCallback("terra", "terra")
        with budget.scope():
            callback.on_chat_model_start({}, [], run_id=identity)
            callback.on_llm_end(result(identity))
        return budget.ledger.total_tokens

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert tuple(pool.map(run, ("a", "b"))) == (5, 5)


def test_completed_run_id_is_reusable_and_cached_usage_is_counted() -> None:
    budget = AgentUsageBudget(controls())
    callback = LangChainUsageCallback("synthesis", "terra")
    cached = AIMessage(
        content="not inspected",
        id="cached-1",
        usage_metadata={
            "input_tokens": 5,
            "output_tokens": 1,
            "total_tokens": 6,
            "input_token_details": {"cache_read": 4},
        },
    )
    with budget.scope():
        callback.on_chat_model_start({}, [], run_id="reused")
        callback.on_llm_end(
            LLMResult(generations=[[ChatGeneration(message=cached)]]),
            run_id="reused",
        )
        callback.on_chat_model_start({}, [], run_id="reused")
        callback.on_llm_end(result("second"), run_id="reused")

    assert budget.attempts == 2
    assert budget.ledger.cached_input_tokens == 4


def test_missing_run_ids_count_conservatively_without_leaking_tracking_state() -> None:
    budget = AgentUsageBudget(controls())
    callback = LangChainUsageCallback("synthesis", "terra")
    with budget.scope():
        callback.on_chat_model_start({}, [])
        callback.on_llm_start({}, [])
    assert budget.attempts == 2
    assert callback._started == set()


def test_over_budget_usage_is_retained_for_accounting() -> None:
    budget = AgentUsageBudget(controls(max_input_tokens=4, max_output_tokens=4, max_total_tokens=4))
    callback = LangChainUsageCallback("synthesis", "terra")
    with budget.scope():
        callback.on_chat_model_start({}, [], run_id="spent")
        with pytest.raises(AgentRuntimeBudgetExceeded):
            callback.on_llm_end(result("spent", prompt=3, completion=2), run_id="spent")
    assert budget.ledger.total_tokens == 5

    next_budget = AgentUsageBudget(controls())
    with next_budget.scope():
        callback.on_chat_model_start({}, [], run_id="spent")
        callback.on_llm_end(result("next"), run_id="spent")
    assert next_budget.attempts == 1


def test_ledger_and_deadline_fail_closed() -> None:
    with pytest.raises(UsageMetadataError):
        ModelUsageLedger(total_tokens=1)
    deadline = RuntimeDeadline(ends_at=10.0, clock=lambda: 10.0)
    with pytest.raises(AgentRuntimeFailure) as error:
        deadline.check()
    assert error.value.code is RuntimeFailureCode.EXECUTION_TIMEOUT
    assert classify_runtime_failure(TimeoutError()) is RuntimeFailureCode.EXECUTION_TIMEOUT
    assert classify_runtime_failure(ConnectionError()) is RuntimeFailureCode.PROVIDER_UNAVAILABLE
    assert classify_runtime_failure(PermissionError()) is None
