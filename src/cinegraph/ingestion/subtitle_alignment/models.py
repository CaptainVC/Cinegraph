from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EpisodeKey:
    season: int
    episode: int


@dataclass(slots=True)
class ScriptDialogue:
    episode_key: EpisodeKey
    speaker: str
    text: str
    order: int


@dataclass(frozen=True, slots=True)
class AlignmentMatch:
    dialogue: ScriptDialogue
    score: float
    margin: float


@dataclass(frozen=True, slots=True)
class UnresolvedLine:
    cue_number: int
    line_number: int
    text: str
    best_speaker: str | None
    best_score: float | None
    reason: str


@dataclass(frozen=True, slots=True)
class SubtitleDialogueLine:
    cue_number: int
    line_number: int
    text: str
    match_text: str
    has_source_label: bool


@dataclass(frozen=True, slots=True)
class AlignmentReport:
    source_pdf: str
    source_subtitle: str
    output_subtitle: str
    episode_key: EpisodeKey
    labelled_lines: int
    fallback_labelled_lines: int
    unresolved_lines: tuple[UnresolvedLine, ...]

    # Processes the supplied to json values.
    def to_json(self) -> str:
        return json.dumps(
            {
                "source_pdf": self.source_pdf,
                "source_subtitle": self.source_subtitle,
                "output_subtitle": self.output_subtitle,
                "episode": {
                    "season": self.episode_key.season,
                    "episode": self.episode_key.episode,
                },
                "labelled_lines": self.labelled_lines,
                "fallback_labelled_lines": self.fallback_labelled_lines,
                "unresolved_lines": [
                    {
                        "cue_number": line.cue_number,
                        "line_number": line.line_number,
                        "text": line.text,
                        "best_speaker": line.best_speaker,
                        "best_score": line.best_score,
                        "reason": line.reason,
                    }
                    for line in self.unresolved_lines
                ],
            },
            indent=2,
        )
