from hashlib import sha256

from cinegraph.config import DEFAULT_SPEAKER_REVIEW_CONFIGURATION
from cinegraph.domain.enums.enum import SpeakerReviewDisposition
from cinegraph.domain.models.transcript import (
    SpeakerReviewCandidate,
    SpeakerReviewDecision,
    SpeakerReviewEvidence,
)
from cinegraph.ingestion.speaker_review.reviewed_output import (
    render_reviewed_subtitle,
)


def test_renders_decided_speaker_and_removes_style_only_cue() -> None:
    source_text = (
        "1\n00:00:01,000 --> 00:00:02,000\nCLAIRE?: Hello there.\n\n"
        "2\n00:00:02,100 --> 00:00:03,000\n<i></i>\n"
    )
    candidate = SpeakerReviewCandidate(
        candidate_id="candidate-1",
        source_filename="Modern Family - 1x01.script-aligned.srt",
        source_sha256=sha256(source_text.encode()).hexdigest(),
        season_number=1,
        episode_number=1,
        cue_number=1,
        line_number=3,
        proposed_speaker="CLAIRE",
        dialogue_text="Hello there.",
        allowed_speakers=("CLAIRE",),
        evidence=(SpeakerReviewEvidence("e1", "screenplay", "CLAIRE", "Hello there."),),
    )
    decision = SpeakerReviewDecision(
        candidate_id="candidate-1",
        disposition=SpeakerReviewDisposition.CONSENSUS_ACCEPTED,
        speaker="CLAIRE",
        reason="agreed",
        primary_verdicts=(),
    )

    rendered, removed_lines, removed_cues = render_reviewed_subtitle(
        source_text=source_text,
        candidates=(candidate,),
        decisions={candidate.candidate_id: decision},
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
    )

    assert rendered == (
        "1\n00:00:01,000 --> 00:00:02,000\nCLAIRE: Hello there.\n"
    )
    assert removed_lines == 1
    assert removed_cues == (2,)
