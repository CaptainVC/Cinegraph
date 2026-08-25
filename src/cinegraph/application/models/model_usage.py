"""Typed, content-free model usage accounting and LangChain integration."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable, Mapping

from cinegraph.application.service.agent_runtime_resilience import AgentRuntimeBudgetExceeded
from cinegraph.config.agent_runtime_controls import AgentRuntimeControlConfiguration

try:
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError as exc:  # mandatory runtime dependency
    raise RuntimeError("langchain-core is required for model usage accounting") from exc


class UsageMetadataError(ValueError):
    pass


_active_budget: ContextVar["AgentUsageBudget | None"] = ContextVar(
    "cinegraph_active_usage_budget", default=None
)
_active_runtime_observer: ContextVar[Callable[["ModelUsageLedger"], None] | None] = ContextVar(
    "cinegraph_active_runtime_usage_observer", default=None
)


@contextmanager
def runtime_usage_observer_scope(observer: Callable[["ModelUsageLedger"], None]) -> Iterator[None]:
    token = _active_runtime_observer.set(observer)
    try:
        yield
    finally:
        _active_runtime_observer.reset(token)


def current_runtime_usage_observer() -> Callable[["ModelUsageLedger"], None] | None:
    return _active_runtime_observer.get()


def _count(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise UsageMetadataError(f"invalid {label}")
    return value


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_micros: int
    model_role: str
    model_name: str
    response_identity: str

    def __post_init__(self) -> None:
        for n in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "total_tokens",
            "cost_micros",
        ):
            _count(getattr(self, n), n)
        if (
            self.cached_input_tokens > self.input_tokens
            or self.total_tokens != self.input_tokens + self.output_tokens
        ):
            raise UsageMetadataError("inconsistent token totals")
        for n in ("model_role", "model_name", "response_identity"):
            v = getattr(self, n)
            if not isinstance(v, str) or not v or len(v) > 256:
                raise UsageMetadataError(f"invalid {n}")


@dataclass(frozen=True, slots=True)
class ModelUsageLedger:
    entries: tuple[ModelUsage, ...] = ()
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_micros: int = 0

    def __post_init__(self) -> None:
        if any(
            isinstance(x, bool) or not isinstance(x, int) or x < 0
            for x in (
                self.input_tokens,
                self.cached_input_tokens,
                self.output_tokens,
                self.total_tokens,
                self.cost_micros,
            )
        ):
            raise UsageMetadataError("invalid ledger totals")
        if (
            self.total_tokens != self.input_tokens + self.output_tokens
            or self.cached_input_tokens > self.input_tokens
        ):
            raise UsageMetadataError("inconsistent ledger totals")
        if not isinstance(self.entries, tuple) or any(
            not isinstance(item, ModelUsage) for item in self.entries
        ):
            raise UsageMetadataError("invalid ledger entries")
        identities: set[tuple[str, str, str]] = set()
        if (
            sum(item.input_tokens for item in self.entries) != self.input_tokens
            or sum(item.cached_input_tokens for item in self.entries) != self.cached_input_tokens
            or sum(item.output_tokens for item in self.entries) != self.output_tokens
            or sum(item.total_tokens for item in self.entries) != self.total_tokens
            or sum(item.cost_micros for item in self.entries) != self.cost_micros
        ):
            raise UsageMetadataError("ledger totals do not match entries")
        for item in self.entries:
            key = (item.model_role, item.model_name, item.response_identity)
            if key in identities:
                raise UsageMetadataError("duplicate ledger response metadata")
            identities.add(key)

    def add(self, usage: ModelUsage) -> "ModelUsageLedger":
        for existing in self.entries:
            if (
                existing.response_identity == usage.response_identity
                and existing.model_role == usage.model_role
                and existing.model_name == usage.model_name
            ):
                if existing != usage:
                    raise UsageMetadataError("conflicting duplicate response metadata")
                return self
        return ModelUsageLedger(
            self.entries + (usage,),
            self.input_tokens + usage.input_tokens,
            self.cached_input_tokens + usage.cached_input_tokens,
            self.output_tokens + usage.output_tokens,
            self.total_tokens + usage.total_tokens,
            self.cost_micros + usage.cost_micros,
        )


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def extract_model_usage(
    response: Any, *, model_role: str, model_name: str, controls: AgentRuntimeControlConfiguration
) -> ModelUsage:
    identity = (
        _get(response, "id")
        or _get(_get(response, "response_metadata"), "id")
        or _get(_get(response, "response_metadata"), "response_id")
    )
    if not isinstance(identity, str) or not identity or len(identity) > 256:
        if controls.usage_required:
            raise UsageMetadataError("stable response identity is required")
        identity = f"ephemeral:{id(response)}"
    metadata = _get(response, "usage_metadata") or _get(response, "response_metadata")
    usage = _get(metadata, "usage_metadata") if metadata is not None else None
    usage = usage or metadata
    if usage is None:
        if controls.usage_required:
            raise UsageMetadataError("usage metadata is required")
        usage = {}
    inp = _get(usage, "input_tokens")
    out = _get(usage, "output_tokens")
    cached = _get(usage, "cached_input_tokens")
    if inp is None or out is None:
        token_usage = _get(metadata, "token_usage")
        inp, out = _get(token_usage, "prompt_tokens"), _get(token_usage, "completion_tokens")
        details = _get(token_usage, "prompt_tokens_details")
        cached = cached if cached is not None else _get(details, "cached_tokens")
    if cached is None:
        details = _get(usage, "input_token_details")
        cached = _get(details, "cache_read")
    if inp is None or out is None:
        if controls.usage_required:
            raise UsageMetadataError("input/output usage is required")
        inp = out = cached = 0
    inp, out, cached = (
        _count(inp, "input_tokens"),
        _count(out, "output_tokens"),
        _count(cached or 0, "cached_input_tokens"),
    )
    if cached > inp:
        raise UsageMetadataError("cached input exceeds input")
    rate = controls.rates_by_model.get(model_name) or controls.rates_by_role.get(model_role)
    if rate is None:
        raise UsageMetadataError("model pricing is not configured")
    noncached = inp - cached
    cost = (
        noncached * rate.input_micros_per_million
        + cached * rate.cached_input_micros_per_million
        + out * rate.output_micros_per_million
        + 999_999
    ) // 1_000_000
    return ModelUsage(inp, cached, out, inp + out, cost, model_role, model_name, identity)


class AgentUsageBudget:
    """Thread-safe per-agent ledger; no prompt or completion content is retained."""

    def __init__(self, controls: AgentRuntimeControlConfiguration) -> None:
        self.controls = controls
        self._ledger = ModelUsageLedger()
        self._attempts = 0
        self._lock = Lock()

    @property
    def ledger(self) -> ModelUsageLedger:
        with self._lock:
            return self._ledger

    @property
    def attempts(self) -> int:
        with self._lock:
            return self._attempts

    def start_call(self) -> None:
        with self._lock:
            self._attempts += 1
            limit = getattr(self.controls, "max_model_calls", 0)
            if limit and self._attempts > limit:
                raise AgentRuntimeBudgetExceeded("budget_exceeded")

    def record(self, usage: ModelUsage) -> ModelUsageLedger:
        with self._lock:
            candidate = self._ledger.add(usage)
            # The provider has already consumed this usage. Retain it for
            # accounting even when the response is rejected at the boundary.
            self._ledger = candidate
            if (
                candidate.input_tokens > self.controls.max_input_tokens
                or candidate.output_tokens > self.controls.max_output_tokens
                or candidate.total_tokens > self.controls.max_total_tokens
                or candidate.cost_micros > self.controls.max_estimated_cost_micros
            ):
                raise AgentRuntimeBudgetExceeded("budget_exceeded")
            return candidate

    @contextmanager
    def scope(self) -> Iterator["AgentUsageBudget"]:
        token = _active_budget.set(self)
        try:
            yield self
        finally:
            _active_budget.reset(token)


def current_usage_budget() -> AgentUsageBudget | None:
    return _active_budget.get()


class LangChainUsageCallback(BaseCallbackHandler):
    """Role-bound callback. It is inert unless an agent scope is active."""

    raise_error = True

    def __init__(self, model_role: str, model_name: str) -> None:
        self.model_role, self.model_name = model_role, model_name
        self._started: set[object] = set()
        self._start_lock = Lock()

    def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any) -> None:
        del serialized, prompts
        self._start(kwargs.get("run_id"))

    def on_chat_model_start(
        self, serialized: dict[str, Any], messages: list[Any], **kwargs: Any
    ) -> None:
        del serialized, messages
        self._start(kwargs.get("run_id"))

    def _start(self, run_id: object) -> None:
        budget = current_usage_budget()
        if budget is None:
            return
        if run_id is None:
            # A malformed callback cannot be deduplicated safely. Count it
            # conservatively without retaining an unfinishable sentinel.
            budget.start_call()
            return
        # Some callback managers emit both hooks for adapters; one provider run
        # must count exactly once.
        with self._start_lock:
            if run_id in self._started:
                return
            self._started.add(run_id)
        try:
            budget.start_call()
        except Exception:
            with self._start_lock:
                self._started.discard(run_id)
            raise

    def _finish(self, run_id: object) -> None:
        if run_id is None:
            return
        with self._start_lock:
            self._started.discard(run_id)

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        run_id = kwargs.get("run_id")
        del kwargs
        budget = current_usage_budget()
        if budget is None:
            self._finish(run_id)
            return
        # LLMResult carries generations; inspect metadata only (never content).
        candidates: list[Any] = []
        for row in getattr(response, "generations", ()) or ():
            for generation in row if isinstance(row, (tuple, list)) else (row,):
                candidates.append(getattr(generation, "message", generation))
                info = getattr(generation, "generation_info", None)
                if info is not None:
                    candidates.append(info)
        usage = getattr(response, "llm_output", None)
        if usage is not None:
            candidates.append(usage)
        try:
            for candidate in candidates:
                try:
                    item = extract_model_usage(
                        candidate,
                        model_role=self.model_role,
                        model_name=self.model_name,
                        controls=budget.controls,
                    )
                except UsageMetadataError:
                    continue
                budget.record(item)
                return
            if budget.controls.usage_required:
                raise UsageMetadataError("usage metadata is required")
        finally:
            self._finish(run_id)

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        # Failed calls have no reliable usage; attempts were already counted.
        del error
        self._finish(kwargs.get("run_id"))


def usage_callback(model_role: str, model_name: str) -> LangChainUsageCallback:
    return LangChainUsageCallback(model_role, model_name)
