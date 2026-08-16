from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from cinegraph.common.error_messages import SpeakerReviewErrorMessages
from cinegraph.config import SpeakerReviewConfiguration
from cinegraph.domain.models.transcript import (
    SpeakerReviewCandidate,
    SpeakerReviewEvidence,
)
from cinegraph.ingestion.speaker_review.patterns import (
    ANY_SPEAKER_LABEL_PATTERN,
    UNCERTAIN_SPEAKER_LABEL_PATTERN,
)
from cinegraph.ingestion.subtitle_alignment.matching import score_match
from cinegraph.ingestion.subtitle_alignment.models import EpisodeKey, ScriptDialogue
from cinegraph.ingestion.subtitle_alignment.script_parser import extract_script_dialogue
from cinegraph.ingestion.subtitle_alignment.subtitle_parser import (
    episode_key_from_subtitle_path,
    read_subtitle_text,
)


@dataclass(frozen=True, slots=True)
class _LabelledSubtitleLine:
    cue_number: int
    line_number: int
    speaker: str
    text: str


def build_speaker_review_candidates(
    *,
    source_pdf: Path,
    aligned_subtitles: tuple[Path, ...],
    configuration: SpeakerReviewConfiguration,
) -> tuple[SpeakerReviewCandidate, ...]:
    dialogue_by_episode = extract_script_dialogue(source_pdf)
    candidates: list[SpeakerReviewCandidate] = []
    for subtitle_path in aligned_subtitles:
        episode_key = episode_key_from_subtitle_path(subtitle_path)
        script_dialogue = dialogue_by_episode.get(episode_key)
        if not script_dialogue:
            raise ValueError(
                SpeakerReviewErrorMessages.SCRIPT_DIALOGUE_REQUIRED.format(
                    season=episode_key.season,
                    episode=episode_key.episode,
                )
            )
        candidates.extend(
            _build_file_candidates(
                subtitle_path=subtitle_path,
                episode_key=episode_key,
                script_dialogue=script_dialogue,
                configuration=configuration,
            )
        )
    return tuple(candidates)


def _build_file_candidates(
    *,
    subtitle_path: Path,
    episode_key: EpisodeKey,
    script_dialogue: tuple[ScriptDialogue, ...],
    configuration: SpeakerReviewConfiguration,
) -> tuple[SpeakerReviewCandidate, ...]:
    subtitle_text = read_subtitle_text(subtitle_path)
    raw_lines = subtitle_text.splitlines()
    source_hash = sha256(subtitle_text.encode("utf-8")).hexdigest()
    labelled_lines = _labelled_subtitle_lines(subtitle_text)
    script_speakers = {line.speaker for line in script_dialogue}
    candidates: list[SpeakerReviewCandidate] = []

    for index, labelled_line in enumerate(labelled_lines):
        raw_line = raw_lines[labelled_line.line_number - 1]
        uncertain_match = UNCERTAIN_SPEAKER_LABEL_PATTERN.fullmatch(raw_line.strip())
        if uncertain_match is None:
            continue

        proposed_speaker = uncertain_match.group("speaker").upper()
        allowed_speakers = tuple(sorted({*script_speakers, proposed_speaker}))
        evidence = _build_evidence(
            target=labelled_line,
            target_index=index,
            subtitle_lines=labelled_lines,
            script_dialogue=script_dialogue,
            configuration=configuration,
        )
        identity_material = (
            f"{subtitle_path.name}\0{source_hash}\0{labelled_line.line_number}\0"
            f"{labelled_line.text}"
        )
        suffix = sha256(identity_material.encode("utf-8")).hexdigest()[:10]
        candidate_id = (
            f"S{episode_key.season:02d}E{episode_key.episode:02d}-"
            f"C{labelled_line.cue_number:04d}-L{labelled_line.line_number:05d}-{suffix}"
        )
        candidates.append(
            SpeakerReviewCandidate(
                candidate_id=candidate_id,
                source_filename=subtitle_path.name,
                source_sha256=source_hash,
                season_number=episode_key.season,
                episode_number=episode_key.episode,
                cue_number=labelled_line.cue_number,
                line_number=labelled_line.line_number,
                proposed_speaker=proposed_speaker,
                dialogue_text=uncertain_match.group("text"),
                allowed_speakers=allowed_speakers,
                evidence=evidence,
            )
        )
    return tuple(candidates)


def _labelled_subtitle_lines(subtitle_text: str) -> tuple[_LabelledSubtitleLine, ...]:
    labelled: list[_LabelledSubtitleLine] = []
    cue_number = 0
    for line_number, line in enumerate(subtitle_text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.isdigit():
            cue_number = int(stripped)
            continue
        match = ANY_SPEAKER_LABEL_PATTERN.fullmatch(stripped)
        if match is None:
            continue
        labelled.append(
            _LabelledSubtitleLine(
                cue_number=cue_number,
                line_number=line_number,
                speaker=match.group("speaker").upper(),
                text=match.group("text"),
            )
        )
    return tuple(labelled)


def _build_evidence(
    *,
    target: _LabelledSubtitleLine,
    target_index: int,
    subtitle_lines: tuple[_LabelledSubtitleLine, ...],
    script_dialogue: tuple[ScriptDialogue, ...],
    configuration: SpeakerReviewConfiguration,
) -> tuple[SpeakerReviewEvidence, ...]:
    evidence: list[SpeakerReviewEvidence] = []
    subtitle_start = max(0, target_index - configuration.subtitle_context_radius)
    subtitle_end = min(
        len(subtitle_lines),
        target_index + configuration.subtitle_context_radius + 1,
    )
    for item in subtitle_lines[subtitle_start:subtitle_end]:
        evidence.append(
            SpeakerReviewEvidence(
                evidence_id=f"subtitle-line-{item.line_number}",
                source="subtitle",
                speaker=item.speaker,
                text=item.text,
            )
        )

    ranked_script = sorted(
        script_dialogue,
        key=lambda item: (-score_match(target.text, item.text), item.order),
    )[: configuration.script_match_limit]
    script_by_order = {line.order: line for line in script_dialogue}
    selected_orders: set[int] = set()
    for match in ranked_script:
        for order in range(
            match.order - configuration.script_context_radius,
            match.order + configuration.script_context_radius + 1,
        ):
            if order in script_by_order:
                selected_orders.add(order)

    for order in sorted(selected_orders):
        item = script_by_order[order]
        evidence.append(
            SpeakerReviewEvidence(
                evidence_id=f"script-order-{item.order}",
                source="screenplay",
                speaker=item.speaker,
                text=item.text,
                similarity_score=round(score_match(target.text, item.text), 3),
            )
        )

    unique: dict[str, SpeakerReviewEvidence] = {}
    for item in evidence:
        unique[item.evidence_id] = item
    return tuple(unique.values())


def candidate_from_dict(payload: dict[str, object]) -> SpeakerReviewCandidate:
    episode = payload["episode"]
    if not isinstance(episode, dict):
        raise TypeError("Candidate episode must be an object.")
    evidence_payload = payload["evidence"]
    if not isinstance(evidence_payload, list):
        raise TypeError("Candidate evidence must be a list.")
    return SpeakerReviewCandidate(
        candidate_id=str(payload["candidate_id"]),
        source_filename=str(payload["source_filename"]),
        source_sha256=str(payload["source_sha256"]),
        season_number=int(episode["season"]),
        episode_number=int(episode["episode"]),
        cue_number=int(payload["cue_number"]),
        line_number=int(payload["line_number"]),
        proposed_speaker=str(payload["proposed_speaker"]),
        dialogue_text=str(payload["dialogue_text"]),
        allowed_speakers=tuple(str(item) for item in payload["allowed_speakers"]),
        evidence=tuple(
            SpeakerReviewEvidence(
                evidence_id=str(item["evidence_id"]),
                source=str(item["source"]),
                speaker=str(item["speaker"]),
                text=str(item["text"]),
                similarity_score=(
                    float(item["similarity_score"])
                    if item.get("similarity_score") is not None
                    else None
                ),
            )
            for item in evidence_payload
            if isinstance(item, dict)
        ),
    )
