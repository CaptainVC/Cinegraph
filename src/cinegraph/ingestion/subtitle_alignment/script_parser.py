from pathlib import Path

from pypdf import PdfReader

from cinegraph.common.error_messages import SubtitleErrorMessages
from cinegraph.ingestion.subtitle_alignment.models import EpisodeKey, ScriptDialogue
from cinegraph.ingestion.subtitle_alignment.patterns import (
    EPISODE_HEADER_PATTERN,
    SPEAKER_LINE_PATTERN,
    TITLE_CARD_TEXTS,
)
from cinegraph.ingestion.subtitle_alignment.text import normalize_speaker


def extract_pdf_text(pdf_path: Path) -> str:
    reader = PdfReader(pdf_path)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    if not text.strip():
        raise ValueError(
            SubtitleErrorMessages.PDF_TEXT_EXTRACTION_FAILED.format(pdf_path=pdf_path)
        )
    return text


def extract_script_dialogue(
    pdf_path: Path,
) -> dict[EpisodeKey, tuple[ScriptDialogue, ...]]:
    dialogue_by_episode: dict[EpisodeKey, list[ScriptDialogue]] = {}
    current_episode: EpisodeKey | None = None
    last_dialogue: ScriptDialogue | None = None
    order = 0

    for raw_line in extract_pdf_text(pdf_path).splitlines():
        line = raw_line.strip()
        header = EPISODE_HEADER_PATTERN.fullmatch(line)
        if header:
            current_episode = EpisodeKey(
                season=int(header.group("season")),
                episode=int(header.group("episode")),
            )
            dialogue_by_episode.setdefault(current_episode, [])
            last_dialogue = None
            continue

        if current_episode is None:
            continue

        speaker_line = SPEAKER_LINE_PATTERN.fullmatch(line)
        if speaker_line:
            last_dialogue = ScriptDialogue(
                episode_key=current_episode,
                speaker=normalize_speaker(speaker_line.group("speaker")),
                text=speaker_line.group("text").strip(),
                order=order,
            )
            dialogue_by_episode[current_episode].append(last_dialogue)
            order += 1
            continue

        if _is_stage_direction(line):
            last_dialogue = None
            continue

        if last_dialogue is not None:
            last_dialogue.text = f"{last_dialogue.text} {line}".strip()

    return {key: tuple(dialogue) for key, dialogue in dialogue_by_episode.items()}


def _is_stage_direction(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith("[") and stripped.endswith("]"):
        return True
    return stripped.upper() in TITLE_CARD_TEXTS
