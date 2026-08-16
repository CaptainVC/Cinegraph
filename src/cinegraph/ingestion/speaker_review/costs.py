from __future__ import annotations

import json
from math import ceil

from cinegraph.common.error_messages import SpeakerReviewErrorMessages
from cinegraph.config import ModelTokenPricing, SpeakerReviewConfiguration
from cinegraph.domain.models.transcript import SpeakerReviewVerdict


def estimate_batch_cost_usd(
    *,
    requests: tuple[dict[str, object], ...],
    model: str,
    configuration: SpeakerReviewConfiguration,
) -> float:
    pricing = _pricing(model, configuration)
    input_characters = sum(
        len(json.dumps(request["body"], ensure_ascii=False, separators=(",", ":")))
        for request in requests
    )
    input_tokens = ceil(
        input_characters / configuration.estimated_characters_per_token
    )
    output_tokens = sum(
        int(request["body"]["max_output_tokens"])
        for request in requests
        if isinstance(request.get("body"), dict)
    )
    return _cost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_price=pricing.input_usd_per_million,
        output_price=pricing.output_usd_per_million,
        multiplier=configuration.batch_discount_multiplier,
    )


def partition_batch_requests(
    *,
    requests: tuple[dict[str, object], ...],
    configuration: SpeakerReviewConfiguration,
) -> tuple[tuple[dict[str, object], ...], ...]:
    parts: list[tuple[dict[str, object], ...]] = []
    current: list[dict[str, object]] = []
    current_tokens = 0
    for request in requests:
        request_tokens = _estimated_request_input_tokens(request, configuration)
        if request_tokens > configuration.maximum_enqueued_input_tokens_per_batch:
            raise RuntimeError(
                SpeakerReviewErrorMessages.BATCH_REQUEST_TOKEN_LIMIT_EXCEEDED
            )
        if (
            current
            and current_tokens + request_tokens
            > configuration.maximum_enqueued_input_tokens_per_batch
        ):
            parts.append(tuple(current))
            current = []
            current_tokens = 0
        current.append(request)
        current_tokens += request_tokens
    if current:
        parts.append(tuple(current))
    return tuple(parts)


def actual_batch_cost_usd(
    *,
    verdicts: tuple[SpeakerReviewVerdict, ...],
    configured_model: str,
    configuration: SpeakerReviewConfiguration,
) -> float:
    pricing = _pricing(configured_model, configuration)
    return _cost(
        input_tokens=sum(item.input_tokens for item in verdicts),
        output_tokens=sum(item.output_tokens for item in verdicts),
        input_price=pricing.input_usd_per_million,
        output_price=pricing.output_usd_per_million,
        multiplier=configuration.batch_discount_multiplier,
    )


def actual_batch_output_cost_usd(
    *,
    output_jsonl: str,
    configured_model: str,
    configuration: SpeakerReviewConfiguration,
) -> float:
    """Price every API response with usage, including unparseable model output."""
    input_tokens = 0
    output_tokens = 0
    for raw_line in output_jsonl.splitlines():
        if not raw_line.strip():
            continue
        try:
            result = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        response = result.get("response")
        if not isinstance(response, dict):
            continue
        body = response.get("body")
        if not isinstance(body, dict):
            continue
        usage = body.get("usage")
        if not isinstance(usage, dict):
            continue
        input_tokens += int(usage.get("input_tokens", 0))
        output_tokens += int(usage.get("output_tokens", 0))
    pricing = _pricing(configured_model, configuration)
    return _cost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_price=pricing.input_usd_per_million,
        output_price=pricing.output_usd_per_million,
        multiplier=configuration.batch_discount_multiplier,
    )


def enforce_budget(
    *,
    estimated_cost_usd: float,
    already_spent_usd: float,
    configuration: SpeakerReviewConfiguration,
) -> None:
    total = estimated_cost_usd + already_spent_usd
    if total > configuration.maximum_run_cost_usd:
        raise RuntimeError(
            SpeakerReviewErrorMessages.REVIEW_BUDGET_EXCEEDED.format(
                estimated=total,
                maximum=configuration.maximum_run_cost_usd,
            )
        )


def _cost(
    *,
    input_tokens: int,
    output_tokens: int,
    input_price: float,
    output_price: float,
    multiplier: float,
) -> float:
    return (
        (
            input_tokens * input_price
            + output_tokens * output_price
        )
        / 1_000_000
        * multiplier
    )


def _pricing(
    model: str,
    configuration: SpeakerReviewConfiguration,
) -> ModelTokenPricing:
    pricing = configuration.model_pricing.get(model)
    if pricing is None:
        raise ValueError(
            SpeakerReviewErrorMessages.MODEL_PRICING_NOT_CONFIGURED.format(
                model=model
            )
        )
    return pricing


def _estimated_request_input_tokens(
    request: dict[str, object],
    configuration: SpeakerReviewConfiguration,
) -> int:
    input_characters = len(
        json.dumps(request["body"], ensure_ascii=False, separators=(",", ":"))
    )
    return ceil(
        input_characters / configuration.estimated_characters_per_token
    )
