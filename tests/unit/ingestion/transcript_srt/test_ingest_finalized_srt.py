from pathlib import Path
from uuid import UUID

import pytest

from cinegraph.domain.enums.enum import Language, RightsStatus
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef
from cinegraph.ingestion.transcript_srt import ingest_finalized_srt
from tests.factories import make_episode_ref


SERIES_ID = UUID("00000000-0000-0000-0000-000000000011")
SEASON_ID = UUID("00000000-0000-0000-0000-000000000101")
EPISODE_ID = UUID("00000000-0000-0000-0000-000000001001")
SOURCE_VERSION_ID = UUID("00000000-0000-0000-0000-000000002001")


def episode() -> EpisodeRef:
    return make_episode_ref(
        series_id=SERIES_ID,
        season_id=SEASON_ID,
        episode_id=EPISODE_ID,
    )


def ingest(source_path: Path):
    return ingest_finalized_srt(
        source_path=source_path,
        source_version_id=SOURCE_VERSION_ID,
        episode=episode(),
        language=Language.ENGLISH,
        rights_status=RightsStatus.RESTRICTED,
    )


def test_ingests_finalized_srt_as_canonical_segments(tmp_path) -> None:
    source_path = tmp_path / "synthetic-s01e01.srt"
    source_path.write_text(
        "1\n"
        "00:00:01,110 --> 00:00:02,960\n"
        "CLAIRE: <i>Kids, breakfast!</i>\n"
        "PHIL: Yeah, just a sec.\n\n"
        "2\n"
        "00:00:02,961 --> 00:00:04,000\n"
        "CLAIRE: Phil, would you get them?\n"
        "CLAIRE: Please?\n",
        encoding="utf-8",
    )

    result = ingest(source_path)

    first_segment, second_segment = result.segments
    assert first_segment.start_ms == 1_110
    assert first_segment.end_ms == 2_960
    assert first_segment.text == "Kids, breakfast! Yeah, just a sec."
    assert [candidate.name for candidate in first_segment.speaker_candidates] == [
        "CLAIRE",
        "PHIL",
    ]
    assert all(
        candidate.confidence == 1.0
        for candidate in first_segment.speaker_candidates
    )
    assert first_segment.style_removed is True

    assert second_segment.text == "Phil, would you get them? Please?"
    assert [candidate.name for candidate in second_segment.speaker_candidates] == [
        "CLAIRE"
    ]
    assert result.report.cue_count == 2
    assert result.report.segment_count == 2
    assert result.report.multi_speaker_cue_count == 1
    assert result.report.style_removed_segment_count == 1


def test_reingesting_the_same_source_creates_the_same_segment_ids(tmp_path) -> None:
    source_path = tmp_path / "synthetic-s01e01.srt"
    source_path.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nCLAIRE: Hello there.\n",
        encoding="utf-8",
    )

    first_result = ingest(source_path)
    second_result = ingest(source_path)

    assert first_result.segments == second_result.segments
    assert first_result.segments[0].segment_id == second_result.segments[0].segment_id


def test_rejects_an_unlabeled_finalized_srt_line(tmp_path) -> None:
    source_path = tmp_path / "synthetic-s01e01.srt"
    source_path.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nHello there.\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="verified speaker label"):
        ingest(source_path)


def test_rejects_an_uncertain_speaker_label(tmp_path) -> None:
    source_path = tmp_path / "synthetic-s01e01.srt"
    source_path.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nCLAIRE?: Hello there.\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="verified speaker label"):
        ingest(source_path)


def test_ignores_style_only_line_when_cue_still_contains_dialogue(tmp_path) -> None:
    source_path = tmp_path / "synthetic-s01e01.srt"
    source_path.write_text(
        "1\n"
        "00:00:01,000 --> 00:00:02,000\n"
        "FRANK: <i>\n"
        "FRANK: Merry Christmas Eve.</i>\n",
        encoding="utf-8",
    )

    result = ingest(source_path)

    assert result.segments[0].text == "Merry Christmas Eve."
    assert result.segments[0].style_removed is True
    assert [item.name for item in result.segments[0].speaker_candidates] == ["FRANK"]


def test_skips_cue_when_every_labeled_line_is_style_only(tmp_path) -> None:
    source_path = tmp_path / "synthetic-s01e01.srt"
    source_path.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nFRANK: <i>\n",
        encoding="utf-8",
    )

    result = ingest(source_path)

    assert result.segments == ()
    assert result.report.cue_count == 1
    assert result.report.segment_count == 0
    assert result.report.skipped_non_dialogue_cue_count == 1


def test_reports_overlapping_cues(tmp_path) -> None:
    source_path = tmp_path / "synthetic-s01e01.srt"
    source_path.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nCLAIRE: Hello there.\n\n"
        "2\n00:00:02,500 --> 00:00:04,000\nPHIL: Hi.\n",
        encoding="utf-8",
    )

    result = ingest(source_path)

    assert result.report.overlap_count == 1
