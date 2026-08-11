from rapidfuzz import fuzz

from cinegraph.common.error_messages import SubtitleErrorMessages
from cinegraph.ingestion.subtitle_alignment.models import (
    AlignmentMatch,
    ScriptDialogue,
    SubtitleDialogueLine,
)
from cinegraph.ingestion.subtitle_alignment.text import normalize_text


DEFAULT_SKIP_PENALTY = 40.0
MINIMUM_MATCH_FLOOR = 65.0


# Align subtitle lines to script dialogue with dynamic-programming match and skip paths.
def align_dialogue_lines(
    subtitle_lines: tuple[SubtitleDialogueLine, ...],
    script_dialogue: tuple[ScriptDialogue, ...],
    minimum_score: float,
    match_floor: float | None = None,
    skip_penalty: float = DEFAULT_SKIP_PENALTY,
) -> dict[int, AlignmentMatch]:
    # Derive the minimum score accepted by match transitions.
    subtitle_count = len(subtitle_lines)
    script_count = len(script_dialogue)
    if match_floor is None:
        match_floor = max(MINIMUM_MATCH_FLOOR, minimum_score - 25.0)

    # Cache every subtitle-to-script similarity score used by the table.
    scores = [
        [score_match(subtitle.text, script.text) for script in script_dialogue]
        for subtitle in subtitle_lines
    ]
    values = [
        [float("-inf")] * (script_count + 1)
        for _ in range(subtitle_count + 1)
    ]
    steps: list[list[str | None]] = [
        [None] * (script_count + 1) for _ in range(subtitle_count + 1)
    ]
    values[0][0] = 0.0

    # Seed paths that skip leading script or subtitle lines.
    for script_index in range(1, script_count + 1):
        values[0][script_index] = values[0][script_index - 1] - skip_penalty
        steps[0][script_index] = "skip_script"
    # Choose the highest-scoring match, repeat, or skip transition for each cell.
    for subtitle_index in range(1, subtitle_count + 1):
        values[subtitle_index][0] = values[subtitle_index - 1][0] - skip_penalty
        steps[subtitle_index][0] = "skip_subtitle"

    for subtitle_index in range(1, subtitle_count + 1):
        for script_index in range(1, script_count + 1):
            candidates = [
                (
                    values[subtitle_index][script_index - 1] - skip_penalty,
                    "skip_script",
                ),
                (
                    values[subtitle_index - 1][script_index] - skip_penalty,
                    "skip_subtitle",
                ),
            ]
            score = scores[subtitle_index - 1][script_index - 1]
            if score >= match_floor:
                candidates.extend(
                    [
                        (
                            values[subtitle_index - 1][script_index - 1] + score,
                            "match_next",
                        ),
                        (
                            values[subtitle_index - 1][script_index] + score,
                            "match_repeat",
                        ),
                    ]
                )
            values[subtitle_index][script_index], steps[subtitle_index][script_index] = max(
                candidates,
                key=lambda candidate: candidate[0],
            )

    # Backtrack the optimal transitions into subtitle-line to dialogue matches.
    matches: dict[int, AlignmentMatch] = {}
    subtitle_index = subtitle_count
    script_index = script_count
    while subtitle_index or script_index:
        step = steps[subtitle_index][script_index]
        if step == "match_next":
            score = scores[subtitle_index - 1][script_index - 1]
            matches[subtitle_lines[subtitle_index - 1].line_number] = AlignmentMatch(
                dialogue=script_dialogue[script_index - 1],
                score=score,
                margin=0.0,
            )
            subtitle_index -= 1
            script_index -= 1
        elif step == "match_repeat":
            score = scores[subtitle_index - 1][script_index - 1]
            matches[subtitle_lines[subtitle_index - 1].line_number] = AlignmentMatch(
                dialogue=script_dialogue[script_index - 1],
                score=score,
                margin=0.0,
            )
            subtitle_index -= 1
        elif step == "skip_script":
            script_index -= 1
        elif step == "skip_subtitle":
            subtitle_index -= 1
        else:
            raise RuntimeError(
                SubtitleErrorMessages.SUBTITLE_ALIGNMENT_BACKTRACE_FAILED
            )

    return matches


# Return the best normalized similarity score between subtitle and script text.
def score_match(subtitle_text: str, script_text: str) -> float:
    return _match_scores(subtitle_text, script_text)[0]


# Compute partial and full normalized scores, short-circuiting empty inputs.
def _match_scores(subtitle_text: str, script_text: str) -> tuple[float, float]:
    normalized_subtitle = normalize_text(subtitle_text)
    normalized_script = normalize_text(script_text)
    if len(normalized_subtitle) < 3 or not normalized_script:
        return 0.0, 0.0
    if normalized_subtitle in normalized_script:
        return 100.0, fuzz.ratio(normalized_subtitle, normalized_script)
    partial_score = fuzz.partial_ratio(normalized_subtitle, normalized_script)
    full_score = fuzz.ratio(normalized_subtitle, normalized_script)
    return max(partial_score, full_score), full_score
