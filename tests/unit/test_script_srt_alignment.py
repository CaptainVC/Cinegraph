from pathlib import Path

from cinegraph.ingestion.subtitle_alignment import (
    EpisodeKey,
    ScriptDialogue,
    SubtitleDialogueLine,
    align_dialogue_lines,
    annotate_subtitle_file,
    extract_script_dialogue,
    read_subtitle_text,
)


def test_extract_script_dialogue_groups_entries_by_episode(monkeypatch) -> None:
    script_text = """\
1x01 Pilot
Claire : Kids, breakfast!
Phil : Yeah, just a sec.

1x02 The Bicycle Thief
Jay : Where is the bike?
"""
    monkeypatch.setattr(
        "cinegraph.ingestion.subtitle_alignment.script_parser.extract_pdf_text",
        lambda _: script_text,
    )

    dialogue = extract_script_dialogue(Path("season-one.pdf"))

    assert [line.speaker for line in dialogue[EpisodeKey(1, 1)]] == ["CLAIRE", "PHIL"]
    assert [line.text for line in dialogue[EpisodeKey(1, 2)]] == ["Where is the bike?"]


def test_extract_script_dialogue_keeps_wrapped_colon_text_with_prior_speaker(monkeypatch) -> None:
    script_text = """\
1x01 Pilot
Phil : The first part of the speech.
The work. You're mouth might be saying: Hey, we cool!
Claire : Please stop.
"""
    monkeypatch.setattr(
        "cinegraph.ingestion.subtitle_alignment.script_parser.extract_pdf_text",
        lambda _: script_text,
    )

    dialogue = extract_script_dialogue(Path("season-one.pdf"))[EpisodeKey(1, 1)]

    assert [line.speaker for line in dialogue] == ["PHIL", "CLAIRE"]
    assert dialogue[0].text.endswith("Hey, we cool!")


def test_annotate_subtitle_file_preserves_srt_structure(monkeypatch, tmp_path) -> None:
    script_text = """\
1x01 Pilot
Claire : Kids, breakfast! Kids?
Phil : Yeah, just a sec.
"""
    monkeypatch.setattr(
        "cinegraph.ingestion.subtitle_alignment.script_parser.extract_pdf_text",
        lambda _: script_text,
    )
    subtitle_path = tmp_path / "Modern Family - 1x01 - Pilot.en.srt"
    subtitle_path.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nKids, breakfast!\n\n"
        "2\n00:00:02,100 --> 00:00:03,000\nYeah, just a sec.\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "output.srt"
    report_path = tmp_path / "report.json"

    report = annotate_subtitle_file(
        source_pdf=Path("season-one.pdf"),
        source_subtitle=subtitle_path,
        output_subtitle=output_path,
        report_path=report_path,
    )

    assert output_path.read_text(encoding="utf-8") == (
        "1\n00:00:01,000 --> 00:00:02,000\nCLAIRE: Kids, breakfast!\n\n"
        "2\n00:00:02,100 --> 00:00:03,000\nPHIL: Yeah, just a sec.\n"
    )
    assert report.labelled_lines == 2
    assert not report.unresolved_lines
    assert report_path.is_file()


def test_annotate_subtitle_file_skips_season_title_cards(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "cinegraph.ingestion.subtitle_alignment.script_parser.extract_pdf_text",
        lambda _: "1x01 Pilot\nClaire : Hello there.\n",
    )
    subtitle_path = tmp_path / "Modern Family - 1x01 - Pilot.en.srt"
    subtitle_path.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nSEASON 1 EPISODE 1\n\n"
        "2\n00:00:02,100 --> 00:00:03,000\nHello there.\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "output.srt"
    report_path = tmp_path / "report.json"

    annotate_subtitle_file(
        source_pdf=Path("season-one.pdf"),
        source_subtitle=subtitle_path,
        output_subtitle=output_path,
        report_path=report_path,
    )

    assert output_path.read_text(encoding="utf-8") == (
        "2\n00:00:02,100 --> 00:00:03,000\nCLAIRE: Hello there.\n"
    )


def test_sequence_alignment_uses_dialogue_order_for_repeated_short_text() -> None:
    episode_key = EpisodeKey(1, 1)
    script_dialogue = (
        ScriptDialogue(episode_key, "CLAIRE", "Come on!", 0),
        ScriptDialogue(episode_key, "PHIL", "The important explanation.", 1),
        ScriptDialogue(episode_key, "GLORIA", "Come on!", 2),
    )
    subtitle_lines = (
        SubtitleDialogueLine(1, 1, "Come on!", "Come on!", False),
        SubtitleDialogueLine(2, 2, "The important explanation.", "The important explanation.", False),
        SubtitleDialogueLine(3, 3, "Come on!", "Come on!", False),
    )

    matches = align_dialogue_lines(subtitle_lines, script_dialogue, 92.0)

    assert matches[1].dialogue.speaker == "CLAIRE"
    assert matches[2].dialogue.speaker == "PHIL"
    assert matches[3].dialogue.speaker == "GLORIA"


def test_annotate_subtitle_file_preserves_existing_source_labels(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "cinegraph.ingestion.subtitle_alignment.script_parser.extract_pdf_text",
        lambda _: "1x01 Pilot\nPhil : Hello there.\n",
    )
    subtitle_path = tmp_path / "Modern Family - 1x01 - Pilot.en.srt"
    subtitle_path.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nPHIL: Hello there.\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "output.srt"
    report_path = tmp_path / "report.json"

    annotate_subtitle_file(
        source_pdf=Path("season-one.pdf"),
        source_subtitle=subtitle_path,
        output_subtitle=output_path,
        report_path=report_path,
    )

    assert output_path.read_text(encoding="utf-8") == subtitle_path.read_text(
        encoding="utf-8"
    )


def test_read_subtitle_text_supports_windows_1252(tmp_path) -> None:
    subtitle_path = tmp_path / "episode.srt"
    subtitle_path.write_bytes("1\n00:00:01,000 --> 00:00:02,000\nCaf\xe9\n".encode("cp1252"))

    assert read_subtitle_text(subtitle_path).endswith("Café\n")


def test_annotate_subtitle_file_uses_ordered_fallback_without_unknown(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "cinegraph.ingestion.subtitle_alignment.script_parser.extract_pdf_text",
        lambda _: "1x01 Pilot\nClaire : A very different sentence.\n",
    )
    subtitle_path = tmp_path / "Modern Family - 1x01 - Pilot.en.srt"
    subtitle_path.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nUnrelated subtitle wording.\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "output.srt"
    report_path = tmp_path / "report.json"

    report = annotate_subtitle_file(
        source_pdf=Path("season-one.pdf"),
        source_subtitle=subtitle_path,
        output_subtitle=output_path,
        report_path=report_path,
    )

    assert output_path.read_text(encoding="utf-8").endswith(
        "CLAIRE?: Unrelated subtitle wording.\n"
    )
    assert report.fallback_labelled_lines == 1
    assert report.unresolved_lines[0].best_speaker == "CLAIRE"


def test_annotate_subtitle_file_removes_non_dialogue_noise_cues(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "cinegraph.ingestion.subtitle_alignment.script_parser.extract_pdf_text",
        lambda _: "1x01 Pilot\nClaire : Hello there.\n",
    )
    subtitle_path = tmp_path / "Modern Family - 1x01 - Pilot.en.srt"
    subtitle_path.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nMODERN FAMILY\nSEASON 1 EPISODE 1\n\n"
        "2\n00:00:02,100 --> 00:00:03,000\nSync by Example.net\n\n"
        "3\n00:00:03,100 --> 00:00:04,000\nHello there.\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "output.srt"
    report_path = tmp_path / "report.json"

    annotate_subtitle_file(
        source_pdf=Path("season-one.pdf"),
        source_subtitle=subtitle_path,
        output_subtitle=output_path,
        report_path=report_path,
    )

    assert output_path.read_text(encoding="utf-8") == (
        "3\n00:00:03,100 --> 00:00:04,000\nCLAIRE: Hello there.\n"
    )


def test_annotate_subtitle_file_removes_embedded_website_credit_cues(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "cinegraph.ingestion.subtitle_alignment.script_parser.extract_pdf_text",
        lambda _: "1x01 Pilot\nClaire : Hello there.\n",
    )
    subtitle_path = tmp_path / "Modern Family - 1x01 - Pilot.en.srt"
    subtitle_path.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n"
        "<font color=#38b0de>-=http://example.net=-\n"
        "sync: Example</font>\n\n"
        "2\n00:00:02,100 --> 00:00:03,000\nHello there.\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "output.srt"
    report_path = tmp_path / "report.json"

    annotate_subtitle_file(
        source_pdf=Path("season-one.pdf"),
        source_subtitle=subtitle_path,
        output_subtitle=output_path,
        report_path=report_path,
    )

    assert output_path.read_text(encoding="utf-8") == (
        "2\n00:00:02,100 --> 00:00:03,000\nCLAIRE: Hello there.\n"
    )


def test_annotate_subtitle_file_removes_inline_stage_directions(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "cinegraph.ingestion.subtitle_alignment.script_parser.extract_pdf_text",
        lambda _: "1x01 Pilot\nClaire : Hello there.\n",
    )
    subtitle_path = tmp_path / "Modern Family - 1x01 - Pilot.en.srt"
    subtitle_path.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n[ chuckles ] Hello there. [ applause ]\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "output.srt"
    report_path = tmp_path / "report.json"

    annotate_subtitle_file(
        source_pdf=Path("season-one.pdf"),
        source_subtitle=subtitle_path,
        output_subtitle=output_path,
        report_path=report_path,
    )

    assert output_path.read_text(encoding="utf-8") == (
        "1\n00:00:01,000 --> 00:00:02,000\nCLAIRE: Hello there.\n"
    )