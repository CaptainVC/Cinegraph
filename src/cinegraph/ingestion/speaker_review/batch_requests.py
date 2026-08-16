from __future__ import annotations

from cinegraph.common.speaker_review_prompts import (
    SPEAKER_ADJUDICATION_SYSTEM_PROMPT,
    SPEAKER_FINAL_REVIEW_SYSTEM_PROMPT,
    SPEAKER_REVIEW_SYSTEM_PROMPT,
    render_adjudication_prompt,
    render_final_review_prompt,
    render_primary_review_prompt,
)
from cinegraph.config import SpeakerReviewConfiguration
from cinegraph.config.speaker_review_schema import SPEAKER_REVIEW_RESPONSE_SCHEMA
from cinegraph.domain.models.transcript import (
    SpeakerReviewCandidate,
    SpeakerReviewDecision,
    SpeakerReviewVerdict,
)


def build_primary_batch_requests(
    *,
    candidates: tuple[SpeakerReviewCandidate, ...],
    model: str,
    reasoning_effort: str,
    configuration: SpeakerReviewConfiguration,
) -> tuple[dict[str, object], ...]:
    return tuple(
        _batch_request(
            custom_id=(
                f"{candidate.candidate_id}{configuration.custom_id_separator}"
                f"{pass_id}"
            ),
            model=model,
            reasoning_effort=reasoning_effort,
            system_prompt=SPEAKER_REVIEW_SYSTEM_PROMPT,
            user_prompt=render_primary_review_prompt(candidate.to_dict(), pass_id),
            max_output_tokens=configuration.max_output_tokens,
            configuration=configuration,
        )
        for candidate in candidates
        for pass_id in configuration.primary_pass_ids
    )


def build_adjudication_batch_requests(
    *,
    candidates: tuple[SpeakerReviewCandidate, ...],
    primary_verdicts: dict[str, tuple[SpeakerReviewVerdict, ...]],
    model: str,
    reasoning_effort: str,
    configuration: SpeakerReviewConfiguration,
) -> tuple[dict[str, object], ...]:
    return tuple(
        _batch_request(
            custom_id=(
                f"{candidate.candidate_id}{configuration.custom_id_separator}"
                f"{configuration.adjudication_pass_id}"
            ),
            model=model,
            reasoning_effort=reasoning_effort,
            system_prompt=SPEAKER_ADJUDICATION_SYSTEM_PROMPT,
            user_prompt=render_adjudication_prompt(
                candidate.to_dict(),
                tuple(
                    verdict.to_dict()
                    for verdict in primary_verdicts.get(candidate.candidate_id, ())
                ),
            ),
            max_output_tokens=configuration.adjudication_max_output_tokens,
            configuration=configuration,
        )
        for candidate in candidates
    )


def build_final_review_batch_requests(
    *,
    candidates: tuple[SpeakerReviewCandidate, ...],
    decisions: dict[str, SpeakerReviewDecision],
    model: str,
    reasoning_effort: str,
    configuration: SpeakerReviewConfiguration,
    pass_id: str | None = None,
    max_output_tokens: int | None = None,
) -> tuple[dict[str, object], ...]:
    resolved_pass_id = pass_id or configuration.final_review_pass_id
    resolved_max_output_tokens = (
        max_output_tokens
        if max_output_tokens is not None
        else configuration.final_review_max_output_tokens
    )
    return tuple(
        _batch_request(
            custom_id=(
                f"{candidate.candidate_id}{configuration.custom_id_separator}"
                f"{resolved_pass_id}"
            ),
            model=model,
            reasoning_effort=reasoning_effort,
            system_prompt=SPEAKER_FINAL_REVIEW_SYSTEM_PROMPT,
            user_prompt=render_final_review_prompt(
                candidate.to_dict(),
                tuple(
                    verdict.to_dict()
                    for verdict in decisions[candidate.candidate_id].primary_verdicts
                ),
                (
                    decisions[candidate.candidate_id]
                    .adjudication_verdict.to_dict()
                    if decisions[candidate.candidate_id].adjudication_verdict
                    is not None
                    else None
                ),
            ),
            max_output_tokens=resolved_max_output_tokens,
            configuration=configuration,
        )
        for candidate in candidates
    )


def _batch_request(
    *,
    custom_id: str,
    model: str,
    reasoning_effort: str,
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int,
    configuration: SpeakerReviewConfiguration,
) -> dict[str, object]:
    return {
        "custom_id": custom_id,
        "method": configuration.batch_method,
        "url": configuration.batch_endpoint,
        "body": {
            "model": model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "reasoning": {"effort": reasoning_effort},
            "max_output_tokens": max_output_tokens,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": configuration.response_schema_name,
                    "strict": True,
                    "schema": SPEAKER_REVIEW_RESPONSE_SCHEMA,
                }
            },
        },
    }
