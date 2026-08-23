from pathlib import Path

from cinegraph.common.error_messages import SubtitleErrorMessages
from cinegraph.ingestion.subtitle_alignment.matching import align_dialogue_lines
from cinegraph.ingestion.subtitle_alignment.models import (
    AlignmentReport,
    SubtitleDialogueLine,
    UnresolvedLine,
)
from cinegraph.ingestion.subtitle_alignment.patterns import (
    EXISTING_LABEL_PATTERN,
    TIMECODE_PATTERN,
)
from cinegraph.ingestion.subtitle_alignment.script_parser import extract_script_dialogue
from cinegraph.ingestion.subtitle_alignment.subtitle_parser import (
    episode_key_from_subtitle_path,
    is_dialogue_line,
    read_subtitle_text,
    remove_noise_cues,
)

DEFAULT_MINIMUM_SCORE = 92.0
FALLBACK_MINIMUM_SCORE = 0.0
FALLBACK_MATCH_FLOOR = 0.0
FALLBACK_SKIP_PENALTY = 125.0
FALLBACK_REASON = "Assigned by ordered fallback below the confidence threshold."


# Align a subtitle file to script dialogue, write labels and noise-free output, and report fallbacks.
def annotate_subtitle_file(
    *,
    source_pdf: Path,
    source_subtitle: Path,
    output_subtitle: Path,
    report_path: Path,
    minimum_score: float = DEFAULT_MINIMUM_SCORE,
) -> AlignmentReport:
    # Resolve the episode and load the script dialogue used as the alignment reference.
    episode_key = episode_key_from_subtitle_path(source_subtitle)
    dialogue_by_episode = extract_script_dialogue(source_pdf)
    script_dialogue = dialogue_by_episode.get(episode_key)
    if not script_dialogue:
        raise ValueError(
            SubtitleErrorMessages.SCRIPT_DIALOGUE_NOT_FOUND.format(
                season=episode_key.season,
                episode=episode_key.episode,
            )
        )

    # Extract subtitle dialogue while retaining the original lines for output updates.
    source_lines = read_subtitle_text(source_subtitle).splitlines(keepends=True)
    output_lines = source_lines.copy()
    subtitle_lines = _extract_dialogue_lines(source_lines)
    match_inputs = tuple(
        SubtitleDialogueLine(
            cue_number=line.cue_number,
            line_number=line.line_number,
            text=line.match_text,
            match_text=line.match_text,
            has_source_label=line.has_source_label,
        )
        for line in subtitle_lines
    )
    # Produce strict matches first, then an ordered fallback for unresolved dialogue.
    matches = align_dialogue_lines(match_inputs, script_dialogue, minimum_score)
    fallback_matches = align_dialogue_lines(
        match_inputs,
        script_dialogue,
        minimum_score=FALLBACK_MINIMUM_SCORE,
        match_floor=FALLBACK_MATCH_FLOOR,
        skip_penalty=FALLBACK_SKIP_PENALTY,
    )

    # Apply labels or fallback markers while recording unresolved lines for the report.
    unresolved_lines: list[UnresolvedLine] = []
    labelled_lines = 0
    fallback_labelled_lines = 0
    for subtitle_line in subtitle_lines:
        source_line = source_lines[subtitle_line.line_number - 1]
        line_ending = source_line[len(source_line.rstrip("\r\n")):]
        match = matches.get(subtitle_line.line_number)
        if subtitle_line.has_source_label:
            labelled_lines += 1
            continue
        if match is None or match.score < minimum_score:
            fallback_match = fallback_matches.get(subtitle_line.line_number)
            if fallback_match is None:
                raise RuntimeError(
                    SubtitleErrorMessages.ORDERED_SCRIPT_FALLBACK_NOT_FOUND.format(
                        subtitle_path=source_subtitle,
                        line_number=subtitle_line.line_number,
                    )
                )
            unresolved_lines.append(
                UnresolvedLine(
                    cue_number=subtitle_line.cue_number,
                    line_number=subtitle_line.line_number,
                    text=subtitle_line.match_text,
                    best_speaker=fallback_match.dialogue.speaker,
                    best_score=fallback_match.score,
                    reason=FALLBACK_REASON,
                )
            )
            output_lines[subtitle_line.line_number - 1] = (
                f"{fallback_match.dialogue.speaker}?: {subtitle_line.text}{line_ending}"
            )
            fallback_labelled_lines += 1
            continue

        output_lines[subtitle_line.line_number - 1] = (
            f"{match.dialogue.speaker}: {subtitle_line.text}{line_ending}"
        )
        labelled_lines += 1

    # Persist the canonicalized subtitle and its alignment report.
    output_subtitle.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output_subtitle.write_text("".join(remove_noise_cues(output_lines)), encoding="utf-8")

    report = AlignmentReport(
        source_pdf=str(source_pdf),
        source_subtitle=str(source_subtitle),
        output_subtitle=str(output_subtitle),
        episode_key=episode_key,
        labelled_lines=labelled_lines,
        fallback_labelled_lines=fallback_labelled_lines,
        unresolved_lines=tuple(unresolved_lines),
    )
    report_path.write_text(report.to_json(), encoding="utf-8")
    return report


# Scan subtitle text and retain dialogue lines with cue and source-label metadata.
def _extract_dialogue_lines(source_lines: list[str]) -> list[SubtitleDialogueLine]:
    # Scan subtitle cues and retain only dialogue lines with normalized match text.
    subtitle_lines: list[SubtitleDialogueLine] = []
    cue_number = 0
    for line_number, source_line in enumerate(source_lines, start=1):
        source_text = source_line.rstrip("\r\n")
        if source_text.strip().isdigit():
            cue_number = int(source_text.strip())
            continue
        if TIMECODE_PATTERN.fullmatch(source_text.strip()) or not is_dialogue_line(
            source_text
        ):
            continue
        subtitle_lines.append(
            SubtitleDialogueLine(
                cue_number=cue_number,
                line_number=line_number,
                text=source_text,
                match_text=EXISTING_LABEL_PATTERN.sub("", source_text),
                has_source_label=EXISTING_LABEL_PATTERN.match(source_text) is not None,
            )
        )
    return subtitle_lines
