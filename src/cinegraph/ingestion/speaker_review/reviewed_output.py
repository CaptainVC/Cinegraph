from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from cinegraph.common.error_messages import (
    SpeakerReviewErrorMessages,
    SubtitleErrorMessages,
)
from cinegraph.config import SpeakerReviewConfiguration
from cinegraph.domain.enums.enum import (
    SourceReviewStatus,
    SpeakerReviewDisposition,
)
from cinegraph.domain.models.transcript import (
    SpeakerReviewCandidate,
    SpeakerReviewDecision,
)
from cinegraph.ingestion.speaker_review.patterns import (
    UNCERTAIN_SPEAKER_LABEL_PATTERN,
)
from cinegraph.ingestion.subtitle_alignment.subtitle_parser import (
    episode_key_from_subtitle_path,
    is_dialogue_line,
    is_non_dialogue_noise,
)
from cinegraph.ingestion.transcript_srt.patterns import SrtPatterns


@dataclass(frozen=True, slots=True)
class ReviewedOutputRecord:
    candidate_filename: str
    reviewed_filename: str
    candidate_sha256: str
    reviewed_sha256: str
    automated_decision_count: int
    human_decision_count: int
    consensus_decision_count: int
    adjudicated_decision_count: int
    final_review_decision_count: int
    removed_noise_lines: int
    removed_cue_numbers: tuple[int, ...]


def write_reviewed_outputs(
    *,
    run_directory: Path,
    source_paths: dict[str, Path],
    candidates: tuple[SpeakerReviewCandidate, ...],
    decisions: tuple[SpeakerReviewDecision, ...],
    reviewer_models: tuple[str, ...],
    prompt_version: str,
    actual_cost_usd: float,
    configuration: SpeakerReviewConfiguration,
    human_queue_filename: str | None = None,
    reviewed_at: str | None = None,
) -> tuple[ReviewedOutputRecord, ...]:
    decision_by_id = {item.candidate_id: item for item in decisions}
    candidate_by_file: dict[str, list[SpeakerReviewCandidate]] = {}
    for candidate in candidates:
        candidate_by_file.setdefault(candidate.source_filename, []).append(candidate)

    unresolved = [
        decision
        for decision in decisions
        if decision.disposition is SpeakerReviewDisposition.NEEDS_HUMAN
    ]
    if unresolved:
        queue_filename = human_queue_filename or (
            configuration.remaining_human_queue_filename
            if (run_directory / configuration.initial_human_queue_filename).exists()
            else configuration.initial_human_queue_filename
        )
        _write_human_queue(
            run_directory,
            unresolved,
            candidates,
            queue_filename,
        )
        return ()

    has_human_review = any(
        item.disposition is SpeakerReviewDisposition.HUMAN_REVIEW_ACCEPTED
        for item in decisions
    )
    review_status = (
        SourceReviewStatus.HYBRID_REVIEWED
        if has_human_review
        else SourceReviewStatus.AUTOMATED_REVIEWED
    )
    output_suffix = (
        ".hybrid-reviewed.srt"
        if has_human_review
        else ".automated-reviewed.srt"
    )

    output_root = run_directory / configuration.reviewed_directory_name
    records: list[ReviewedOutputRecord] = []
    for source_filename, source_path in sorted(source_paths.items()):
        file_candidates = candidate_by_file.get(source_filename, [])
        source_text = source_path.read_text(encoding="utf-8")
        reviewed_text, removed_lines, removed_cues = render_reviewed_subtitle(
            source_text=source_text,
            candidates=tuple(file_candidates),
            decisions=decision_by_id,
            configuration=configuration,
        )
        season_number = episode_key_from_subtitle_path(source_path).season
        output_directory = output_root / f"season-{season_number:02d}"
        output_directory.mkdir(parents=True, exist_ok=True)
        output_filename = source_filename.replace(
            ".script-aligned.srt",
            output_suffix,
        )
        output_path = output_directory / output_filename
        _write_if_new_or_unchanged(output_path, reviewed_text)
        file_decisions = [decision_by_id[item.candidate_id] for item in file_candidates]
        records.append(
            ReviewedOutputRecord(
                candidate_filename=source_filename,
                reviewed_filename=str(output_path.relative_to(run_directory)),
                candidate_sha256=_sha256(source_text),
                reviewed_sha256=_sha256(reviewed_text),
                automated_decision_count=sum(
                    item.disposition
                    is not SpeakerReviewDisposition.HUMAN_REVIEW_ACCEPTED
                    for item in file_decisions
                ),
                human_decision_count=sum(
                    item.disposition
                    is SpeakerReviewDisposition.HUMAN_REVIEW_ACCEPTED
                    for item in file_decisions
                ),
                consensus_decision_count=sum(
                    item.disposition is SpeakerReviewDisposition.CONSENSUS_ACCEPTED
                    for item in file_decisions
                ),
                adjudicated_decision_count=sum(
                    item.disposition is SpeakerReviewDisposition.ADJUDICATION_ACCEPTED
                    for item in file_decisions
                ),
                final_review_decision_count=sum(
                    item.disposition
                    is SpeakerReviewDisposition.FINAL_REVIEW_ACCEPTED
                    for item in file_decisions
                ),
                removed_noise_lines=removed_lines,
                removed_cue_numbers=removed_cues,
            )
        )

    ledger_path = run_directory / "review-ledger.json"
    resolved_reviewed_at = reviewed_at or datetime.now(UTC).isoformat()
    if ledger_path.exists():
        prior_ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        resolved_reviewed_at = str(prior_ledger["reviewed_at"])
    ledger = {
        "schema_version": configuration.ledger_schema_version,
        "review_status": review_status.value,
        "reviewed_by": list(reviewer_models),
        "reviewed_at": resolved_reviewed_at,
        "prompt_version": prompt_version,
        "actual_batch_cost_usd": round(actual_cost_usd, 8),
        "records": [asdict(record) for record in records],
        "decisions": [item.to_dict() for item in decisions],
    }
    _write_if_new_or_unchanged(
        ledger_path,
        json.dumps(ledger, indent=2, ensure_ascii=False) + "\n",
    )
    _write_calibration_sample(
        run_directory=run_directory,
        decisions=decisions,
        candidates=candidates,
        sample_size=configuration.calibration_sample_size,
    )
    return tuple(records)


def render_reviewed_subtitle(
    *,
    source_text: str,
    candidates: tuple[SpeakerReviewCandidate, ...],
    decisions: dict[str, SpeakerReviewDecision],
    configuration: SpeakerReviewConfiguration,
) -> tuple[str, int, tuple[int, ...]]:
    source_hash = _sha256(source_text)
    lines = source_text.splitlines()
    for candidate in candidates:
        if candidate.source_sha256 != source_hash:
            raise ValueError("Candidate source hash does not match subtitle content.")
        decision = decisions.get(candidate.candidate_id)
        if decision is None or decision.speaker is None:
            raise ValueError(
                SpeakerReviewErrorMessages.REVIEW_DECISION_MISSING.format(
                    candidate_id=candidate.candidate_id
                )
            )
        source_line = lines[candidate.line_number - 1].strip()
        match = UNCERTAIN_SPEAKER_LABEL_PATTERN.fullmatch(source_line)
        if match is None or match.group("text") != candidate.dialogue_text:
            raise ValueError("Candidate line changed after review preparation.")
        lines[candidate.line_number - 1] = (
            f"{decision.speaker}: {match.group('text')}"
        )

    output_blocks: list[str] = []
    removed_line_count = 0
    removed_cue_numbers: list[int] = []
    for block in SrtPatterns.CUE_SEPARATOR.split("\n".join(lines).strip()):
        if not block.strip():
            continue
        block_lines = block.splitlines()
        if (
            len(block_lines) < 2
            or not block_lines[0].strip().isdigit()
            or SrtPatterns.TIMECODE.fullmatch(block_lines[1].strip()) is None
        ):
            raise ValueError(
                SubtitleErrorMessages.MALFORMED_SRT_CUE_FOR_PROMOTION.format(
                    filename="automated-review"
                )
            )
        cue_number = int(block_lines[0].strip())
        retained: list[str] = []
        for line in block_lines[2:]:
            stripped = line.strip()
            if not stripped:
                continue
            if (
                stripped in configuration.redaction_placeholders
                or is_non_dialogue_noise(stripped)
                or not is_dialogue_line(stripped)
            ):
                removed_line_count += 1
                continue
            if SrtPatterns.VERIFIED_SPEAKER_LABEL_PATTERN.fullmatch(stripped):
                retained.append(stripped)
                continue
            raise ValueError(
                SubtitleErrorMessages.UNREVIEWED_SUBTITLE_LINE_FOR_PROMOTION.format(
                    filename="automated-review",
                    cue_number=cue_number,
                    line=stripped,
                )
            )
        if retained:
            output_blocks.append(
                "\n".join(
                    [block_lines[0].strip(), block_lines[1].strip(), *retained]
                )
            )
        else:
            removed_cue_numbers.append(cue_number)
    return (
        "\n\n".join(output_blocks) + "\n",
        removed_line_count,
        tuple(removed_cue_numbers),
    )


def _write_human_queue(
    run_directory: Path,
    unresolved: list[SpeakerReviewDecision],
    candidates: tuple[SpeakerReviewCandidate, ...],
    filename: str,
) -> None:
    candidate_by_id = {item.candidate_id: item for item in candidates}
    payload = [
        {
            "candidate": candidate_by_id[item.candidate_id].to_dict(),
            "decision": item.to_dict(),
        }
        for item in unresolved
    ]
    _write_if_new_or_unchanged(
        run_directory / filename,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )


def _write_calibration_sample(
    *,
    run_directory: Path,
    decisions: tuple[SpeakerReviewDecision, ...],
    candidates: tuple[SpeakerReviewCandidate, ...],
    sample_size: int,
) -> None:
    candidate_by_id = {item.candidate_id: item for item in candidates}
    selected = sorted(
        decisions,
        key=lambda item: sha256(item.candidate_id.encode("utf-8")).hexdigest(),
    )[:sample_size]
    payload = [
        {
            "candidate_id": item.candidate_id,
            "episode": {
                "season": candidate_by_id[item.candidate_id].season_number,
                "episode": candidate_by_id[item.candidate_id].episode_number,
            },
            "cue_number": candidate_by_id[item.candidate_id].cue_number,
            "dialogue_text": candidate_by_id[item.candidate_id].dialogue_text,
            "selected_speaker": item.speaker,
            "disposition": item.disposition.value,
            "human_verdict": None,
        }
        for item in selected
    ]
    _write_if_new_or_unchanged(
        run_directory / "calibration-sample.json",
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )


def _write_if_new_or_unchanged(path: Path, content: str) -> None:
    if path.exists() and path.read_text(encoding="utf-8") != content:
        raise FileExistsError(
            SpeakerReviewErrorMessages.REVIEWED_OUTPUT_CONFLICT.format(path=path)
        )
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()
