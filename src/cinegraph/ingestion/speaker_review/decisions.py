from __future__ import annotations

from cinegraph.config import SpeakerReviewConfiguration
from cinegraph.domain.enums.enum import (
    SpeakerReviewAction,
    SpeakerReviewDisposition,
)
from cinegraph.domain.models.transcript import (
    SpeakerReviewCandidate,
    SpeakerReviewDecision,
    SpeakerReviewVerdict,
)


def decide_primary_consensus(
    *,
    candidates: tuple[SpeakerReviewCandidate, ...],
    verdicts: dict[str, tuple[SpeakerReviewVerdict, ...]],
    configuration: SpeakerReviewConfiguration,
) -> tuple[SpeakerReviewDecision, ...]:
    decisions: list[SpeakerReviewDecision] = []
    for candidate in candidates:
        candidate_verdicts = verdicts.get(candidate.candidate_id, ())
        accepted, reason = _has_primary_consensus(
            candidate_verdicts,
            configuration,
        )
        decisions.append(
            SpeakerReviewDecision(
                candidate_id=candidate.candidate_id,
                disposition=(
                    SpeakerReviewDisposition.CONSENSUS_ACCEPTED
                    if accepted
                    else SpeakerReviewDisposition.ADJUDICATION_REQUIRED
                ),
                speaker=candidate_verdicts[0].speaker if accepted else None,
                reason=reason,
                primary_verdicts=candidate_verdicts,
            )
        )
    return tuple(decisions)


def apply_adjudication(
    *,
    primary_decisions: tuple[SpeakerReviewDecision, ...],
    adjudication_verdicts: dict[str, tuple[SpeakerReviewVerdict, ...]],
    configuration: SpeakerReviewConfiguration,
) -> tuple[SpeakerReviewDecision, ...]:
    final: list[SpeakerReviewDecision] = []
    for decision in primary_decisions:
        if decision.disposition is SpeakerReviewDisposition.CONSENSUS_ACCEPTED:
            final.append(decision)
            continue
        verdict_group = adjudication_verdicts.get(decision.candidate_id, ())
        verdict = verdict_group[0] if len(verdict_group) == 1 else None
        accepted = (
            verdict is not None
            and verdict.action is not SpeakerReviewAction.NEEDS_REVIEW
            and verdict.confidence >= configuration.adjudication_minimum_confidence
            and bool(verdict.evidence_ids)
        )
        final.append(
            SpeakerReviewDecision(
                candidate_id=decision.candidate_id,
                disposition=(
                    SpeakerReviewDisposition.ADJUDICATION_ACCEPTED
                    if accepted
                    else SpeakerReviewDisposition.NEEDS_HUMAN
                ),
                speaker=verdict.speaker if accepted and verdict is not None else None,
                reason=(
                    "Adjudicator met the evidence and confidence policy."
                    if accepted
                    else "Adjudicator did not meet the automatic acceptance policy."
                ),
                primary_verdicts=decision.primary_verdicts,
                adjudication_verdict=verdict,
            )
        )
    return tuple(final)


def apply_final_review(
    *,
    decisions: tuple[SpeakerReviewDecision, ...],
    final_verdicts: dict[str, tuple[SpeakerReviewVerdict, ...]],
    configuration: SpeakerReviewConfiguration,
) -> tuple[SpeakerReviewDecision, ...]:
    final: list[SpeakerReviewDecision] = []
    for decision in decisions:
        if decision.disposition is not SpeakerReviewDisposition.NEEDS_HUMAN:
            final.append(decision)
            continue
        verdict_group = final_verdicts.get(decision.candidate_id, ())
        verdict = verdict_group[0] if len(verdict_group) == 1 else None
        accepted = (
            verdict is not None
            and verdict.action is not SpeakerReviewAction.NEEDS_REVIEW
            and verdict.confidence >= configuration.final_review_minimum_confidence
            and bool(verdict.evidence_ids)
        )
        final.append(
            SpeakerReviewDecision(
                candidate_id=decision.candidate_id,
                disposition=(
                    SpeakerReviewDisposition.FINAL_REVIEW_ACCEPTED
                    if accepted
                    else SpeakerReviewDisposition.NEEDS_HUMAN
                ),
                speaker=verdict.speaker if accepted and verdict is not None else None,
                reason=(
                    "Final reviewer met the evidence and confidence policy."
                    if accepted
                    else "Final reviewer did not meet the automatic acceptance policy."
                ),
                primary_verdicts=decision.primary_verdicts,
                adjudication_verdict=decision.adjudication_verdict,
                final_review_verdict=verdict,
            )
        )
    return tuple(final)


def _has_primary_consensus(
    verdicts: tuple[SpeakerReviewVerdict, ...],
    configuration: SpeakerReviewConfiguration,
) -> tuple[bool, str]:
    expected_passes = set(configuration.primary_pass_ids)
    if len(verdicts) != len(expected_passes) or {
        verdict.pass_id for verdict in verdicts
    } != expected_passes:
        return False, "One or more primary verdicts are missing."
    if any(verdict.action is SpeakerReviewAction.NEEDS_REVIEW for verdict in verdicts):
        return False, "A primary reviewer requested escalation."
    if len({verdict.speaker for verdict in verdicts}) != 1:
        return False, "Primary reviewers selected different speakers."
    if any(
        verdict.confidence < configuration.consensus_minimum_confidence
        for verdict in verdicts
    ):
        return False, "Primary confidence is below the consensus threshold."
    if any(not verdict.evidence_ids for verdict in verdicts):
        return False, "A primary verdict did not cite evidence."
    return True, "Both primary reviewers independently agreed above threshold."
