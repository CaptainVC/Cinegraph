from io import BytesIO
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


# Extract text from every PDF page and reject PDFs with no usable text.
def extract_pdf_text(pdf_path: Path) -> str:
    reader = PdfReader(pdf_path)
    return _extract_pdf_text(reader, str(pdf_path))


# Extract PDF text from bytes already captured by the private-source boundary.
def extract_pdf_text_content(content: bytes, source_name: str) -> str:
    reader = PdfReader(BytesIO(content))
    return _extract_pdf_text(reader, source_name)


def _extract_pdf_text(reader: PdfReader, source_name: str) -> str:
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    if not text.strip():
        raise ValueError(
            SubtitleErrorMessages.PDF_TEXT_EXTRACTION_FAILED.format(
                pdf_path=source_name
            )
        )
    return text


# Parse episode headers and speaker dialogue, including continuation lines, from a script PDF.
def extract_script_dialogue(
    pdf_path: Path,
) -> dict[EpisodeKey, tuple[ScriptDialogue, ...]]:
    return _parse_script_dialogue(extract_pdf_text(pdf_path))


def extract_script_dialogue_content(
    content: bytes,
    source_name: str,
) -> dict[EpisodeKey, tuple[ScriptDialogue, ...]]:
    return _parse_script_dialogue(extract_pdf_text_content(content, source_name))


def _parse_script_dialogue(
    content: str,
) -> dict[EpisodeKey, tuple[ScriptDialogue, ...]]:
    # Walk script lines, switching episodes and accumulating ordered dialogue.
    dialogue_by_episode: dict[EpisodeKey, list[ScriptDialogue]] = {}
    current_episode: EpisodeKey | None = None
    last_dialogue: ScriptDialogue | None = None
    order = 0

    # Classify each line as an episode header, speaker line, stage direction, or continuation.
    for raw_line in content.splitlines():
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


# Return whether a script line is blank, bracketed stage direction, or title-card text.
def _is_stage_direction(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith("[") and stripped.endswith("]"):
        return True
    return stripped.upper() in TITLE_CARD_TEXTS
