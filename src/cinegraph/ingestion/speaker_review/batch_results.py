from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict

from cinegraph.common.error_messages import SpeakerReviewErrorMessages
from cinegraph.config import (
    DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
    SpeakerReviewConfiguration,
)
from cinegraph.domain.enums.enum import SpeakerReviewAction
from cinegraph.domain.models.transcript import (
    SpeakerReviewCandidate,
    SpeakerReviewVerdict,
)


class SpeakerReviewStructuredOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    action: SpeakerReviewAction
    speaker: str
    confidence: float
    evidence_ids: list[str]
    rationale: str


def parse_batch_results(
    *,
    output_jsonl: str,
    candidates: dict[str, SpeakerReviewCandidate],
    configuration: SpeakerReviewConfiguration = DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
) -> tuple[dict[str, tuple[SpeakerReviewVerdict, ...]], tuple[dict[str, object], ...]]:
    verdicts: dict[str, list[SpeakerReviewVerdict]] = {}
    errors: list[dict[str, object]] = []
    for raw_line in output_jsonl.splitlines():
        if not raw_line.strip():
            continue
        try:
            result = json.loads(raw_line)
            verdict = _parse_result_line(
                result=result,
                candidates=candidates,
                configuration=configuration,
            )
        except Exception as error:  # Persist exact request context for controlled escalation.
            custom_id = "unknown"
            try:
                custom_id = str(json.loads(raw_line).get("custom_id", "unknown"))
            except json.JSONDecodeError:
                pass
            errors.append(
                {
                    "custom_id": custom_id,
                    "error_type": type(error).__name__,
                    "message": str(error),
                }
            )
            continue
        verdicts.setdefault(verdict.candidate_id, []).append(verdict)

    normalized = {
        candidate_id: tuple(sorted(items, key=lambda item: item.pass_id))
        for candidate_id, items in verdicts.items()
    }
    return normalized, tuple(errors)


def _parse_result_line(
    *,
    result: dict[str, object],
    candidates: dict[str, SpeakerReviewCandidate],
    configuration: SpeakerReviewConfiguration,
) -> SpeakerReviewVerdict:
    custom_id = str(result["custom_id"])
    candidate_id, pass_id = custom_id.rsplit(
        configuration.custom_id_separator,
        maxsplit=1,
    )
    candidate = candidates[candidate_id]
    response = result.get("response")
    if not isinstance(response, dict) or int(response.get("status_code", 0)) != 200:
        raise ValueError(
            SpeakerReviewErrorMessages.BATCH_REQUEST_FAILED.format(custom_id=custom_id)
        )
    body = response.get("body")
    if not isinstance(body, dict):
        raise ValueError(SpeakerReviewErrorMessages.MODEL_RESPONSE_MALFORMED)
    output_text = _extract_output_text(body)
    structured = SpeakerReviewStructuredOutput.model_validate_json(output_text)
    if structured.candidate_id != candidate_id:
        raise ValueError(SpeakerReviewErrorMessages.CANDIDATE_IDENTIFIER_MISMATCH)
    speaker = structured.speaker.strip().upper()
    if speaker not in candidate.allowed_speakers:
        raise ValueError(SpeakerReviewErrorMessages.MODEL_SPEAKER_NOT_ALLOWED)
    evidence_ids = tuple(dict.fromkeys(structured.evidence_ids))
    if not set(evidence_ids).issubset(candidate.evidence_ids):
        raise ValueError(SpeakerReviewErrorMessages.MODEL_EVIDENCE_NOT_ALLOWED)
    usage = body.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    return SpeakerReviewVerdict(
        candidate_id=candidate_id,
        pass_id=pass_id,
        action=structured.action,
        speaker=speaker,
        confidence=structured.confidence,
        evidence_ids=evidence_ids,
        rationale=structured.rationale.strip(),
        model=str(body.get("model", "unknown")),
        response_id=str(body.get("id", "unknown")),
        input_tokens=int(usage.get("input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
    )


def _extract_output_text(body: dict[str, object]) -> str:
    direct = body.get("output_text")
    if isinstance(direct, str) and direct:
        return direct
    output = body.get("output")
    if not isinstance(output, list):
        raise ValueError(SpeakerReviewErrorMessages.MODEL_RESPONSE_MALFORMED)
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if (
                isinstance(part, dict)
                and part.get("type") == "output_text"
                and isinstance(part.get("text"), str)
            ):
                return str(part["text"])
    raise ValueError(SpeakerReviewErrorMessages.MODEL_RESPONSE_MALFORMED)
