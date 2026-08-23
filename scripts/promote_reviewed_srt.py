from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from cinegraph.common.error_messages import SubtitleErrorMessages
from cinegraph.ingestion.transcript_srt.patterns import SrtPatterns

QUESTION_MARK_SPEAKER_LABEL_PATTERN = re.compile(
    r"^(?P<speaker>[A-Za-z][A-Za-z -]{0,48})\?:\s*(?P<text>.+)$"
)
REDACTION_PLACEHOLDERS = frozenset({"***", "- ***.", "--"})
REVIEW_LEDGER_FILENAME = "review-ledger.json"


@dataclass(frozen=True, slots=True)
class ReviewedSrtPromotionRecord:
    candidate_filename: str
    reviewed_filename: str
    candidate_sha256: str
    reviewed_sha256: str
    promoted_question_mark_labels: int
    removed_redaction_lines: int
    removed_cue_numbers: tuple[int, ...]


def promote_reviewed_srt_directory(
    *,
    candidate_directory: Path,
    reviewed_directory: Path,
    reviewed_by: str,
) -> tuple[ReviewedSrtPromotionRecord, ...]:
    if not reviewed_by or reviewed_by.strip() != reviewed_by:
        raise ValueError(SubtitleErrorMessages.REVIEWED_BY_MUST_BE_TRIMMED)

    candidate_paths = tuple(sorted(candidate_directory.glob("*.script-aligned.srt")))
    if not candidate_paths:
        raise ValueError(
            SubtitleErrorMessages.NO_SCRIPT_ALIGNED_SRT_FILES_FOUND
        )

    reviewed_directory.mkdir(parents=True, exist_ok=True)
    records = tuple(
        _promote_file(
            candidate_path=candidate_path,
            reviewed_directory=reviewed_directory,
        )
        for candidate_path in candidate_paths
    )
    _write_ledger(
        reviewed_directory=reviewed_directory,
        reviewed_by=reviewed_by,
        records=records,
    )
    return records


def _promote_file(
    *,
    candidate_path: Path,
    reviewed_directory: Path,
) -> ReviewedSrtPromotionRecord:
    candidate_text = candidate_path.read_text(encoding="utf-8")
    reviewed_text, promoted_count, removed_line_count, removed_cue_numbers = (
        _promote_text(candidate_text, candidate_path)
    )
    reviewed_filename = candidate_path.name.replace(
        ".script-aligned.srt",
        ".reviewed.srt",
    )
    reviewed_path = reviewed_directory / reviewed_filename
    _write_if_new_or_unchanged(reviewed_path, reviewed_text)
    return ReviewedSrtPromotionRecord(
        candidate_filename=candidate_path.name,
        reviewed_filename=reviewed_filename,
        candidate_sha256=_sha256(candidate_text),
        reviewed_sha256=_sha256(reviewed_text),
        promoted_question_mark_labels=promoted_count,
        removed_redaction_lines=removed_line_count,
        removed_cue_numbers=removed_cue_numbers,
    )


def _promote_text(
    candidate_text: str,
    candidate_path: Path,
) -> tuple[str, int, int, tuple[int, ...]]:
    output_blocks: list[str] = []
    promoted_count = 0
    removed_line_count = 0
    removed_cue_numbers: list[int] = []

    for block in SrtPatterns.CUE_SEPARATOR.split(candidate_text.strip()):
        if not block.strip():
            continue
        lines = block.splitlines()
        if len(lines) < 2:
            raise ValueError(
                SubtitleErrorMessages.MALFORMED_SRT_CUE_FOR_PROMOTION.format(
                    filename=candidate_path.name
                )
            )

        cue_number = int(lines[0].strip())
        promoted_lines: list[str] = []
        for line in lines[2:]:
            stripped = line.strip()
            if not stripped:
                continue

            uncertain_match = QUESTION_MARK_SPEAKER_LABEL_PATTERN.fullmatch(stripped)
            if uncertain_match is not None:
                promoted_lines.append(
                    f"{uncertain_match.group('speaker')}: "
                    f"{uncertain_match.group('text')}"
                )
                promoted_count += 1
                continue

            if SrtPatterns.VERIFIED_SPEAKER_LABEL_PATTERN.fullmatch(stripped):
                promoted_lines.append(stripped)
                continue

            if stripped in REDACTION_PLACEHOLDERS:
                removed_line_count += 1
                continue

            raise ValueError(
                SubtitleErrorMessages.UNREVIEWED_SUBTITLE_LINE_FOR_PROMOTION.format(
                    filename=candidate_path.name,
                    cue_number=cue_number,
                    line=stripped,
                )
            )

        if not promoted_lines:
            removed_cue_numbers.append(cue_number)
            continue

        output_blocks.append(
            "\n".join([lines[0].strip(), lines[1].strip(), *promoted_lines])
        )

    return (
        "\n\n".join(output_blocks) + "\n",
        promoted_count,
        removed_line_count,
        tuple(removed_cue_numbers),
    )


def _write_if_new_or_unchanged(path: Path, content: str) -> None:
    if path.exists() and path.read_text(encoding="utf-8") != content:
        raise FileExistsError(
            SubtitleErrorMessages.REVIEWED_SRT_CONTENT_CONFLICT.format(path=path)
        )
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def _write_ledger(
    *,
    reviewed_directory: Path,
    reviewed_by: str,
    records: tuple[ReviewedSrtPromotionRecord, ...],
) -> None:
    ledger = {
        "schema_version": 1,
        "review_status": "reviewed",
        "reviewed_by": reviewed_by,
        "reviewed_at": datetime.now(UTC).isoformat(),
        "records": [asdict(record) for record in records],
    }
    (reviewed_directory / REVIEW_LEDGER_FILENAME).write_text(
        json.dumps(ledger, indent=2) + "\n",
        encoding="utf-8",
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Promote manually reviewed script-aligned subtitles."
    )
    parser.add_argument("candidate_directory", type=Path)
    parser.add_argument("reviewed_directory", type=Path)
    parser.add_argument("--reviewed-by", required=True)
    arguments = parser.parse_args()

    records = promote_reviewed_srt_directory(
        candidate_directory=arguments.candidate_directory,
        reviewed_directory=arguments.reviewed_directory,
        reviewed_by=arguments.reviewed_by,
    )
    print(
        {
            "reviewed_files": len(records),
            "promoted_question_mark_labels": sum(
                record.promoted_question_mark_labels for record in records
            ),
            "removed_redaction_lines": sum(
                record.removed_redaction_lines for record in records
            ),
            "removed_cues": sum(len(record.removed_cue_numbers) for record in records),
        }
    )


if __name__ == "__main__":
    main()
