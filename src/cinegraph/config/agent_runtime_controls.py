"""Operator-owned limits and accounting assumptions for agent model calls.

Rates are deliberately integer micros-per-million values.  They are accounting
assumptions, not a claim about a provider's current price sheet.
"""

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


def _positive(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class ModelTokenRate:
    input_micros_per_million: int
    cached_input_micros_per_million: int
    output_micros_per_million: int

    def __post_init__(self) -> None:
        for n in (
            "input_micros_per_million",
            "cached_input_micros_per_million",
            "output_micros_per_million",
        ):
            _positive(getattr(self, n), n)


@dataclass(frozen=True, slots=True)
class AgentRuntimeControlConfiguration:
    max_model_calls: int = 8
    max_input_tokens: int = 100_000
    max_output_tokens: int = 20_000
    max_total_tokens: int = 120_000
    max_estimated_cost_micros: int = 2_000_000
    max_execution_duration_seconds: int = 120
    usage_required: bool = True
    rates_by_model: Mapping[str, ModelTokenRate] = field(
        default_factory=lambda: {
            "gpt-5.6-terra": ModelTokenRate(5_000_000, 500_000, 15_000_000),
            "gpt-5.6-luna": ModelTokenRate(1_000_000, 100_000, 3_000_000),
        }
    )
    rates_by_role: Mapping[str, ModelTokenRate] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for n in (
            "max_model_calls",
            "max_input_tokens",
            "max_output_tokens",
            "max_total_tokens",
            "max_estimated_cost_micros",
            "max_execution_duration_seconds",
        ):
            _positive(getattr(self, n), n)
        if self.max_total_tokens < max(self.max_input_tokens, self.max_output_tokens):
            raise ValueError("max_total_tokens must cover input and output limits")
        if not isinstance(self.usage_required, bool):
            raise ValueError("usage_required must be boolean")
        for mapping in (self.rates_by_model, self.rates_by_role):
            if any(
                not isinstance(k, str) or not k or not isinstance(v, ModelTokenRate)
                for k, v in mapping.items()
            ):
                raise ValueError("pricing keys and values must be valid")
        object.__setattr__(self, "rates_by_model", MappingProxyType(dict(self.rates_by_model)))
        object.__setattr__(self, "rates_by_role", MappingProxyType(dict(self.rates_by_role)))


DEFAULT_AGENT_RUNTIME_CONTROLS = AgentRuntimeControlConfiguration()
