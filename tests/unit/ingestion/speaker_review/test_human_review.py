import json
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from cinegraph.config import DEFAULT_SPEAKER_REVIEW_CONFIGURATION
from cinegraph.domain.enums.enum import (
    SourceReviewStatus,
    SpeakerReviewDisposition,
    SpeakerReviewRunStatus,
)
from cinegraph.domain.models.transcript import (
    SpeakerReviewCandidate,
    SpeakerReviewDecision,
    SpeakerReviewEvidence,
)
from cinegraph.ingestion.speaker_review.human_review import (
    HumanSpeakerReviewWorkflow,
)
from cinegraph.ingestion.speaker_review.workflow import (
    SpeakerReviewRunState,
    load_run_state,
    save_run_state,
)

SOURCE_FILENAME = "Modern Family - 1x01.script-aligned.srt"
SOURCE_TEXT = "1\n00:00:01,000 --> 00:00:02,000\nCLAIRE?: Hello there.\n"
REVIEWED_AT = datetime(2026, 8, 16, 10, 0, tzinfo=UTC)


def _candidate() -> SpeakerReviewCandidate:
    return SpeakerReviewCandidate(
        candidate_id="S01E01-C0001-L00003-abcdef1234",
        source_filename=SOURCE_FILENAME,
        source_sha256=sha256(SOURCE_TEXT.encode()).hexdigest(),
        season_number=1,
        episode_number=1,
        cue_number=1,
        line_number=3,
        proposed_speaker="CLAIRE",
        dialogue_text="Hello there.",
        allowed_speakers=("CLAIRE", "PHIL"),
        evidence=(
            SpeakerReviewEvidence(
                evidence_id="script-order-1",
                source="screenplay",
                speaker="PHIL",
                text="Hello there.",
                similarity_score=100.0,
            ),
        ),
    )


def _decision() -> SpeakerReviewDecision:
    return SpeakerReviewDecision(
        candidate_id=_candidate().candidate_id,
        disposition=SpeakerReviewDisposition.NEEDS_HUMAN,
        speaker=None,
        reason="Automated reviewers remained uncertain.",
        primary_verdicts=(),
    )


def _state() -> SpeakerReviewRunState:
    return SpeakerReviewRunState(
        schema_version=2,
        run_id="speaker-review-test",
        status=SpeakerReviewRunStatus.NEEDS_HUMAN,
        created_at="2026-08-16T09:00:00+00:00",
        updated_at="2026-08-16T09:30:00+00:00",
        candidate_count=1,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        final_review_model="gpt-5.6-sol",
        prompt_version="speaker-review-v1",
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=0.1,
        actual_primary_cost_usd=0.01,
        actual_adjudication_cost_usd=0.02,
        actual_final_review_cost_usd=0.03,
        primary_part_count=1,
        primary_completed_part_count=1,
        accepted_by_consensus=0,
        accepted_by_adjudication=0,
        accepted_by_final_review=0,
        needs_human=1,
    )


def _write_run(tmp_path: Path) -> tuple[Path, str]:
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    source_path = tmp_path / SOURCE_FILENAME
    source_path.write_text(SOURCE_TEXT, encoding="utf-8")
    save_run_state(run_directory, _state())
    (run_directory / "candidates.jsonl").write_text(
        json.dumps(_candidate().to_dict()) + "\n",
        encoding="utf-8",
    )
    (run_directory / "final-decisions.jsonl").write_text(
        json.dumps(_decision().to_dict()) + "\n",
        encoding="utf-8",
    )
    (run_directory / "source-manifest.json").write_text(
        json.dumps({"sources": {SOURCE_FILENAME: str(source_path)}}) + "\n",
        encoding="utf-8",
    )
    queue_text = (
        json.dumps(
            [{"candidate": _candidate().to_dict(), "decision": _decision().to_dict()}],
            indent=2,
        )
        + "\n"
    )
    (run_directory / "human-review-queue.json").write_text(
        queue_text,
        encoding="utf-8",
    )
    return run_directory, sha256(queue_text.encode()).hexdigest()


def _write_resolution(
    tmp_path: Path,
    *,
    queue_sha256: str,
    speaker: str = "PHIL",
    candidate_id: str | None = None,
) -> Path:
    path = tmp_path / "resolution.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "speaker-review-test",
                "queue_sha256": queue_sha256,
                "reviewer": "corpus-owner",
                "reviewed_at": REVIEWED_AT.isoformat(),
                "decisions": [
                    {
                        "candidate_id": candidate_id or _candidate().candidate_id,
                        "speaker": speaker,
                        "rationale": "The screenplay context identifies Phil.",
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_prepares_deterministic_offline_workbench(tmp_path: Path) -> None:
    run_directory, queue_hash = _write_run(tmp_path)
    workflow = HumanSpeakerReviewWorkflow(DEFAULT_SPEAKER_REVIEW_CONFIGURATION)

    first = workflow.prepare_workbench(run_directory)
    second = workflow.prepare_workbench(run_directory)
    content = first.path.read_text(encoding="utf-8")

    assert first == second
    assert first.candidate_count == 1
    assert first.queue_sha256 == queue_hash
    assert "speaker-review-test" in content
    assert "Hello there." in content
    assert "http://" not in content
    assert "https://" not in content
    assert "Offline workbench" in content
    assert 'JSON.stringify(output,null,2)+"\\n"' in content
    assert "data:application/json;charset=utf-8," in content
    assert "Copy resolution JSON" in content


def test_workbench_escapes_script_breakout_and_unicode_line_separators(
    tmp_path: Path,
) -> None:
    run_directory, _ = _write_run(tmp_path)
    queue_path = run_directory / "human-review-queue.json"
    queue_path.write_text(
        queue_path.read_text(encoding="utf-8").replace(
            "Hello there.",
            "</script><script>alert(1)</script>\u2028next",
        ),
        encoding="utf-8",
    )

    content = (
        HumanSpeakerReviewWorkflow(DEFAULT_SPEAKER_REVIEW_CONFIGURATION)
        .prepare_workbench(run_directory)
        .path.read_text(encoding="utf-8")
    )

    assert content.count("</script>") == 1
    assert "\\u003c/script\\u003e" in content
    assert "\u2028" not in content
    assert "\\u2028" in content


def test_prepare_uses_latest_versioned_retry_queue(tmp_path: Path) -> None:
    run_directory, _ = _write_run(tmp_path)
    retry_queue_text = (
        (run_directory / "human-review-queue.json")
        .read_text(encoding="utf-8")
        .replace("Automated reviewers remained uncertain.", "Sol remained uncertain.")
    )
    (run_directory / "remaining-human-review-queue-retry-1.json").write_text(
        retry_queue_text,
        encoding="utf-8",
    )
    save_run_state(
        run_directory,
        replace(
            _state(),
            final_review_part_count=2,
            final_review_completed_part_count=2,
            final_review_retry_count=1,
        ),
    )

    result = HumanSpeakerReviewWorkflow(
        DEFAULT_SPEAKER_REVIEW_CONFIGURATION
    ).prepare_workbench(run_directory)

    assert result.queue_sha256 == sha256(retry_queue_text.encode()).hexdigest()
    assert "remaining-human-review-queue-retry-1.json" in result.path.read_text(
        encoding="utf-8"
    )


def test_applies_complete_resolution_and_promotes_hybrid_reviewed_srt(
    tmp_path: Path,
) -> None:
    run_directory, queue_hash = _write_run(tmp_path)
    resolution_path = _write_resolution(tmp_path, queue_sha256=queue_hash)
    workflow = HumanSpeakerReviewWorkflow(DEFAULT_SPEAKER_REVIEW_CONFIGURATION)

    result = workflow.apply_resolution(
        run_directory=run_directory,
        resolution_path=resolution_path,
    )

    assert result.state.status is SpeakerReviewRunStatus.COMPLETED
    assert (
        result.state.schema_version
        == DEFAULT_SPEAKER_REVIEW_CONFIGURATION.schema_version
    )
    assert result.state.accepted_by_human == 1
    assert result.state.needs_human == 0
    assert result.resolution_count == 1
    reviewed_path = (
        run_directory
        / "reviewed"
        / "season-01"
        / "Modern Family - 1x01.hybrid-reviewed.srt"
    )
    assert reviewed_path.read_text(encoding="utf-8") == (
        "1\n00:00:01,000 --> 00:00:02,000\nPHIL: Hello there.\n"
    )
    ledger = json.loads(
        (run_directory / "review-ledger.json").read_text(encoding="utf-8")
    )
    assert ledger["review_status"] == SourceReviewStatus.HYBRID_REVIEWED.value
    assert ledger["reviewed_at"] == REVIEWED_AT.isoformat()
    assert ledger["records"][0]["automated_decision_count"] == 0
    assert ledger["records"][0]["human_decision_count"] == 1
    assert ledger["decisions"][0]["human_review_resolution"]["reviewer"] == (
        "corpus-owner"
    )
    persisted_state = load_run_state(run_directory / "run-state.json")
    assert persisted_state.schema_version == (
        DEFAULT_SPEAKER_REVIEW_CONFIGURATION.schema_version
    )
    assert persisted_state.accepted_by_human == 1

    repeated = workflow.apply_resolution(
        run_directory=run_directory,
        resolution_path=resolution_path,
    )
    assert repeated.state.status is SpeakerReviewRunStatus.COMPLETED
    assert repeated.resolution_count == 1
    assert repeated.records == ()


def test_applies_resolution_to_latest_retry_decisions(tmp_path: Path) -> None:
    run_directory, _ = _write_run(tmp_path)
    queue_text = (run_directory / "human-review-queue.json").read_text(encoding="utf-8")
    (run_directory / "remaining-human-review-queue-retry-1.json").write_text(
        queue_text,
        encoding="utf-8",
    )
    (run_directory / "post-final-decisions-retry-1.jsonl").write_text(
        (run_directory / "final-decisions.jsonl").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    save_run_state(
        run_directory,
        replace(
            _state(),
            final_review_part_count=2,
            final_review_completed_part_count=2,
            final_review_retry_count=1,
        ),
    )
    resolution_path = _write_resolution(
        tmp_path,
        queue_sha256=sha256(queue_text.encode()).hexdigest(),
    )

    result = HumanSpeakerReviewWorkflow(
        DEFAULT_SPEAKER_REVIEW_CONFIGURATION
    ).apply_resolution(
        run_directory=run_directory,
        resolution_path=resolution_path,
    )

    assert result.state.status is SpeakerReviewRunStatus.COMPLETED
    assert result.state.accepted_by_human == 1


def test_rejects_resolution_when_queue_hash_changed(tmp_path: Path) -> None:
    run_directory, _ = _write_run(tmp_path)
    resolution_path = _write_resolution(tmp_path, queue_sha256="f" * 64)

    with pytest.raises(ValueError, match="queue changed"):
        HumanSpeakerReviewWorkflow(
            DEFAULT_SPEAKER_REVIEW_CONFIGURATION
        ).apply_resolution(
            run_directory=run_directory,
            resolution_path=resolution_path,
        )

    assert not (run_directory / "reviewed").exists()


def test_rejects_incomplete_or_duplicate_resolution_set(tmp_path: Path) -> None:
    run_directory, queue_hash = _write_run(tmp_path)
    resolution_path = _write_resolution(
        tmp_path,
        queue_sha256=queue_hash,
        candidate_id="different-candidate",
    )

    with pytest.raises(ValueError, match="every queued candidate exactly once"):
        HumanSpeakerReviewWorkflow(
            DEFAULT_SPEAKER_REVIEW_CONFIGURATION
        ).apply_resolution(
            run_directory=run_directory,
            resolution_path=resolution_path,
        )


def test_rejects_speaker_outside_candidate_allowlist(tmp_path: Path) -> None:
    run_directory, queue_hash = _write_run(tmp_path)
    resolution_path = _write_resolution(
        tmp_path,
        queue_sha256=queue_hash,
        speaker="JAY",
    )

    with pytest.raises(ValueError, match="outside the candidate allowlist"):
        HumanSpeakerReviewWorkflow(
            DEFAULT_SPEAKER_REVIEW_CONFIGURATION
        ).apply_resolution(
            run_directory=run_directory,
            resolution_path=resolution_path,
        )


def test_prepare_rejects_completed_run(tmp_path: Path) -> None:
    run_directory, _ = _write_run(tmp_path)
    save_run_state(
        run_directory,
        replace(_state(), status=SpeakerReviewRunStatus.COMPLETED),
    )

    with pytest.raises(RuntimeError, match="incompatible"):
        HumanSpeakerReviewWorkflow(
            DEFAULT_SPEAKER_REVIEW_CONFIGURATION
        ).prepare_workbench(run_directory)
