from pathlib import Path

from cinegraph.common.error_messages import SubtitleErrorMessages
from cinegraph.ingestion.subtitle_alignment.models import EpisodeKey
from cinegraph.ingestion.subtitle_alignment.patterns import (
    BRACKETED_STAGE_DIRECTION_PATTERN,
    EXISTING_LABEL_PATTERN,
    NON_DIALOGUE_NOISE_PATTERN,
    SUBTITLE_ENCODINGS,
    SUBTITLE_EPISODE_PATTERN,
    SYNC_CREDIT_PREFIXES,
)
from cinegraph.ingestion.subtitle_alignment.text import normalize_text


def read_subtitle_text(subtitle_path: Path) -> str:
    for encoding in SUBTITLE_ENCODINGS:
        try:
            return subtitle_path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(
        SubtitleErrorMessages.SUBTITLE_FILE_DECODE_FAILED.format(
            subtitle_path=subtitle_path
        )
    )


def episode_key_from_subtitle_path(subtitle_path: Path) -> EpisodeKey:
    match = SUBTITLE_EPISODE_PATTERN.search(subtitle_path.name)
    if match is None:
        raise ValueError(
            SubtitleErrorMessages.SUBTITLE_EPISODE_NOT_FOUND.format(
                subtitle_name=subtitle_path.name
            )
        )
    return EpisodeKey(
        season=int(match.group("season")),
        episode=int(match.group("episode")),
    )


def is_dialogue_line(line: str) -> bool:
    text = EXISTING_LABEL_PATTERN.sub("", line).strip()
    if not text or is_non_dialogue_noise(text):
        return False
    text_without_markup = _remove_markup(text)
    if not normalize_text(text_without_markup):
        return False
    normalized = normalize_text(text_without_markup)
    if normalized.startswith("modern family"):
        return False
    return not _is_episode_title(normalized)


def is_non_dialogue_noise(line: str) -> bool:
    text = EXISTING_LABEL_PATTERN.sub("", line).strip()
    if not text:
        return True
    lowered = text.casefold()
    if "www." in lowered or "http" in lowered or lowered.startswith(
        SYNC_CREDIT_PREFIXES
    ):
        return True
    if NON_DIALOGUE_NOISE_PATTERN.match(text):
        return True

    without_stage_directions = BRACKETED_STAGE_DIRECTION_PATTERN.sub(" ", text).strip()
    if not without_stage_directions:
        return True

    normalized = normalize_text(_remove_markup(without_stage_directions))
    return normalized.startswith("modern family") or _is_episode_title(normalized)


def remove_noise_cues(lines: list[str]) -> list[str]:
    filtered_lines: list[str] = []
    cue: list[str] = []

    def append_cue() -> None:
        if not cue:
            return
        retained_content = [
            _clean_generated_dialogue_line(line)
            for line in cue[2:]
            if line.strip()
        ]
        retained_content = [line for line in retained_content if line is not None]
        if retained_content:
            filtered_lines.extend([*cue[:2], *retained_content])

    for line in lines:
        if not line.strip():
            append_cue()
            cue = []
            if filtered_lines and filtered_lines[-1].strip():
                filtered_lines.append(line)
            continue
        cue.append(line)

    append_cue()
    return filtered_lines


def _clean_generated_dialogue_line(line: str) -> str | None:
    line_ending = line[len(line.rstrip("\r\n")):]
    text = line.rstrip("\r\n")
    label = EXISTING_LABEL_PATTERN.match(text)
    prefix = label.group(0) if label else ""
    dialogue = text[len(prefix):]
    dialogue = BRACKETED_STAGE_DIRECTION_PATTERN.sub(" ", dialogue).strip()
    if is_non_dialogue_noise(dialogue):
        return None
    return f"{prefix}{dialogue}{line_ending}"


def _is_episode_title(normalized: str) -> bool:
    import re

    return re.fullmatch(r"season \d+ episode \d+", normalized) is not None


def _remove_markup(text: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", text)
