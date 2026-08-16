import json
from dataclasses import replace
from math import ceil

from cinegraph.config import DEFAULT_SPEAKER_REVIEW_CONFIGURATION
from cinegraph.domain.enums.enum import (
    SpeakerReviewAction,
    SpeakerReviewDisposition,
)
from cinegraph.domain.models.transcript import (
    SpeakerReviewCandidate,
    SpeakerReviewDecision,
    SpeakerReviewEvidence,
    SpeakerReviewVerdict,
)
from cinegraph.ingestion.speaker_review.batch_requests import (
    build_adjudication_batch_requests,
    build_final_review_batch_requests,
    build_primary_batch_requests,
)
from cinegraph.ingestion.speaker_review.batch_results import parse_batch_results
from cinegraph.ingestion.speaker_review.decisions import (
    apply_final_review,
    decide_primary_consensus,
)
from cinegraph.ingestion.speaker_review.costs import (
    actual_batch_output_cost_usd,
    partition_batch_requests,
)


def candidate() -> SpeakerReviewCandidate:
    return SpeakerReviewCandidate(
        candidate_id="S01E01-C0001-L00003-abcdef1234",
        source_filename="episode.script-aligned.srt",
        source_sha256="a" * 64,
        season_number=1,
        episode_number=1,
        cue_number=1,
        line_number=3,
        proposed_speaker="CLAIRE",
        dialogue_text="Kids, breakfast!",
        allowed_speakers=("CLAIRE", "PHIL"),
        evidence=(
            SpeakerReviewEvidence(
                evidence_id="script-order-1",
                source="screenplay",
                speaker="CLAIRE",
                text="Kids, breakfast!",
                similarity_score=100.0,
            ),
        ),
    )


def verdict(pass_id: str, *, speaker: str = "CLAIRE", confidence: float = 0.99):
    return SpeakerReviewVerdict(
        candidate_id=candidate().candidate_id,
        pass_id=pass_id,
        action=SpeakerReviewAction.ACCEPT_CANDIDATE,
        speaker=speaker,
        confidence=confidence,
        evidence_ids=("script-order-1",),
        rationale="Exact screenplay evidence.",
        model="gpt-5.6-luna",
        response_id=f"response-{pass_id}",
        input_tokens=100,
        output_tokens=20,
    )


def test_primary_batch_uses_two_structured_responses_requests() -> None:
    requests = build_primary_batch_requests(
        candidates=(candidate(),),
        model="gpt-5.6-luna",
        reasoning_effort="low",
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
    )

    assert len(requests) == 2
    assert {item["custom_id"].rsplit("::", 1)[1] for item in requests} == {
        "primary-a",
        "primary-b",
    }
    for request in requests:
        body = request["body"]
        assert request["url"] == "/v1/responses"
        assert body["model"] == "gpt-5.6-luna"
        assert body["store"] is False
        assert body["text"]["format"]["strict"] is True


def test_partitions_requests_below_centralized_enqueued_token_limit() -> None:
    requests = build_primary_batch_requests(
        candidates=(candidate(),),
        model="gpt-5.6-luna",
        reasoning_effort="low",
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
    )
    one_request_tokens = ceil(
        len(json.dumps(requests[0]["body"], separators=(",", ":")))
        / DEFAULT_SPEAKER_REVIEW_CONFIGURATION.estimated_characters_per_token
    )
    configuration = replace(
        DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        maximum_enqueued_input_tokens_per_batch=one_request_tokens,
    )

    parts = partition_batch_requests(
        requests=requests,
        configuration=configuration,
    )

    assert parts == ((requests[0],), (requests[1],))


def test_adjudication_uses_its_larger_centralized_output_limit() -> None:
    requests = build_adjudication_batch_requests(
        candidates=(candidate(),),
        primary_verdicts={
            candidate().candidate_id: (
                verdict("primary-a"),
                verdict("primary-b", speaker="PHIL"),
            )
        },
        model="gpt-5.6-terra",
        reasoning_effort="medium",
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
    )

    assert requests[0]["body"]["max_output_tokens"] == 640


def test_final_review_uses_sol_and_accepts_only_policy_compliant_verdict() -> None:
    prior = SpeakerReviewDecision(
        candidate_id=candidate().candidate_id,
        disposition=SpeakerReviewDisposition.NEEDS_HUMAN,
        speaker=None,
        reason="adjudication remained uncertain",
        primary_verdicts=(verdict("primary-a"), verdict("primary-b")),
        adjudication_verdict=verdict("adjudication", confidence=0.80),
    )
    requests = build_final_review_batch_requests(
        candidates=(candidate(),),
        decisions={candidate().candidate_id: prior},
        model="gpt-5.6-sol",
        reasoning_effort="high",
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
    )
    accepted = apply_final_review(
        decisions=(prior,),
        final_verdicts={
            candidate().candidate_id: (
                verdict("final-review", confidence=0.93),
            )
        },
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
    )

    assert requests[0]["body"]["model"] == "gpt-5.6-sol"
    assert requests[0]["body"]["max_output_tokens"] == 1_200
    assert accepted[0].disposition is SpeakerReviewDisposition.FINAL_REVIEW_ACCEPTED


def test_final_review_retry_uses_centralized_larger_output_limit() -> None:
    prior = SpeakerReviewDecision(
        candidate_id=candidate().candidate_id,
        disposition=SpeakerReviewDisposition.NEEDS_HUMAN,
        speaker=None,
        reason="final reviewer did not return structured output",
        primary_verdicts=(verdict("primary-a"), verdict("primary-b")),
    )

    requests = build_final_review_batch_requests(
        candidates=(candidate(),),
        decisions={candidate().candidate_id: prior},
        model="gpt-5.6-sol",
        reasoning_effort="high",
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        pass_id="final-review-retry-1",
        max_output_tokens=2_400,
    )

    assert requests[0]["custom_id"].endswith("::final-review-retry-1")
    assert requests[0]["body"]["max_output_tokens"] == 2_400


def test_actual_cost_includes_usage_from_unparseable_model_output() -> None:
    line = {
        "custom_id": f"{candidate().candidate_id}::final-review",
        "response": {
            "status_code": 200,
            "body": {
                "model": "gpt-5.6-sol",
                "output": [],
                "usage": {"input_tokens": 1_000, "output_tokens": 1_200},
            },
        },
    }

    cost = actual_batch_output_cost_usd(
        output_jsonl=json.dumps(line),
        configured_model="gpt-5.6-sol",
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
    )

    assert cost == 0.0205


def test_parses_responses_batch_output_and_validates_evidence() -> None:
    payload = {
        "candidate_id": candidate().candidate_id,
        "action": "accept_candidate",
        "speaker": "CLAIRE",
        "confidence": 0.99,
        "evidence_ids": ["script-order-1"],
        "rationale": "Exact screenplay evidence.",
    }
    line = {
        "custom_id": f"{candidate().candidate_id}::primary-a",
        "response": {
            "status_code": 200,
            "body": {
                "id": "response-1",
                "model": "gpt-5.6-luna",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": json.dumps(payload)}
                        ],
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 20},
            },
        },
    }

    parsed, errors = parse_batch_results(
        output_jsonl=json.dumps(line),
        candidates={candidate().candidate_id: candidate()},
    )

    assert not errors
    assert parsed[candidate().candidate_id][0].speaker == "CLAIRE"
    assert parsed[candidate().candidate_id][0].input_tokens == 100


def test_consensus_requires_matching_high_confidence_evidence() -> None:
    accepted = decide_primary_consensus(
        candidates=(candidate(),),
        verdicts={
            candidate().candidate_id: (
                verdict("primary-a"),
                verdict("primary-b"),
            )
        },
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
    )
    escalated = decide_primary_consensus(
        candidates=(candidate(),),
        verdicts={
            candidate().candidate_id: (
                verdict("primary-a"),
                verdict("primary-b", speaker="PHIL"),
            )
        },
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
    )

    assert accepted[0].disposition is SpeakerReviewDisposition.CONSENSUS_ACCEPTED
    assert escalated[0].disposition is SpeakerReviewDisposition.ADJUDICATION_REQUIRED
