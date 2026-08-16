from pathlib import Path

from cinegraph.config import DEFAULT_SPEAKER_REVIEW_CONFIGURATION
from cinegraph.ingestion.speaker_review.candidates import (
    build_speaker_review_candidates,
)
from cinegraph.ingestion.subtitle_alignment.models import EpisodeKey, ScriptDialogue


def test_builds_stable_candidate_with_subtitle_and_script_evidence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    episode_key = EpisodeKey(1, 1)
    monkeypatch.setattr(
        "cinegraph.ingestion.speaker_review.candidates.extract_script_dialogue",
        lambda _: {
            episode_key: (
                ScriptDialogue(episode_key, "CLAIRE", "Kids, breakfast!", 0),
                ScriptDialogue(episode_key, "PHIL", "Yeah, just a sec.", 1),
            )
        },
    )
    subtitle_path = tmp_path / "Modern Family - 1x01 - Pilot.script-aligned.srt"
    subtitle_path.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nCLAIRE?: Kids, breakfast!\n\n"
        "2\n00:00:02,100 --> 00:00:03,000\nPHIL: Yeah, just a sec.\n",
        encoding="utf-8",
    )

    candidates = build_speaker_review_candidates(
        source_pdf=tmp_path / "script.pdf",
        aligned_subtitles=(subtitle_path,),
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.candidate_id.startswith("S01E01-C0001-L00003-")
    assert candidate.proposed_speaker == "CLAIRE"
    assert candidate.allowed_speakers == ("CLAIRE", "PHIL")
    assert {item.source for item in candidate.evidence} == {
        "subtitle",
        "screenplay",
    }
    assert "script-order-0" in candidate.evidence_ids
