from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from cinegraph.domain.enums.enum import (
    SpeakerReviewAction,
    SpeakerReviewDisposition,
    SpeakerReviewRunStatus,
)


TERMINAL_SPEAKER_REVIEW_RUN_STATUSES = frozenset(
    {
        SpeakerReviewRunStatus.COMPLETED,
        SpeakerReviewRunStatus.NEEDS_HUMAN,
        SpeakerReviewRunStatus.FAILED,
    }
)


@dataclass(frozen=True, slots=True)
class SpeakerReviewEvidence:
    evidence_id: str
    source: str
    speaker: str
    text: str
    similarity_score: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "source": self.source,
            "speaker": self.speaker,
            "text": self.text,
            "similarity_score": self.similarity_score,
        }


@dataclass(frozen=True, slots=True)
class SpeakerReviewCandidate:
    candidate_id: str
    source_filename: str
    source_sha256: str
    season_number: int
    episode_number: int
    cue_number: int
    line_number: int
    proposed_speaker: str
    dialogue_text: str
    allowed_speakers: tuple[str, ...]
    evidence: tuple[SpeakerReviewEvidence, ...]

    def __post_init__(self) -> None:
        if self.proposed_speaker not in self.allowed_speakers:
            raise ValueError("The proposed speaker must be in the episode allowlist.")
        if not self.evidence:
            raise ValueError("A speaker review candidate requires evidence.")

    @property
    def evidence_ids(self) -> frozenset[str]:
        return frozenset(item.evidence_id for item in self.evidence)

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "source_filename": self.source_filename,
            "source_sha256": self.source_sha256,
            "episode": {
                "season": self.season_number,
                "episode": self.episode_number,
            },
            "cue_number": self.cue_number,
            "line_number": self.line_number,
            "proposed_speaker": self.proposed_speaker,
            "dialogue_text": self.dialogue_text,
            "allowed_speakers": list(self.allowed_speakers),
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class SpeakerReviewVerdict:
    candidate_id: str
    pass_id: str
    action: SpeakerReviewAction
    speaker: str
    confidence: float
    evidence_ids: tuple[str, ...]
    rationale: str
    model: str
    response_id: str
    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        if not isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Speaker review confidence must be between zero and one.")
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("Token counts cannot be negative.")

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "pass_id": self.pass_id,
            "action": self.action.value,
            "speaker": self.speaker,
            "confidence": self.confidence,
            "evidence_ids": list(self.evidence_ids),
            "rationale": self.rationale,
            "model": self.model,
            "response_id": self.response_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


@dataclass(frozen=True, slots=True)
class SpeakerReviewDecision:
    candidate_id: str
    disposition: SpeakerReviewDisposition
    speaker: str | None
    reason: str
    primary_verdicts: tuple[SpeakerReviewVerdict, ...]
    adjudication_verdict: SpeakerReviewVerdict | None = None
    final_review_verdict: SpeakerReviewVerdict | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "disposition": self.disposition.value,
            "speaker": self.speaker,
            "reason": self.reason,
            "primary_verdicts": [item.to_dict() for item in self.primary_verdicts],
            "adjudication_verdict": (
                self.adjudication_verdict.to_dict()
                if self.adjudication_verdict is not None
                else None
            ),
            "final_review_verdict": (
                self.final_review_verdict.to_dict()
                if self.final_review_verdict is not None
                else None
            ),
        }
