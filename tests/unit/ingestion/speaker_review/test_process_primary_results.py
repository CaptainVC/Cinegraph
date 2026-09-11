import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from cinegraph.config import DEFAULT_SPEAKER_REVIEW_CONFIGURATION
from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.domain.models.transcript import (
    SpeakerReviewCandidate,
    SpeakerReviewEvidence,
)
from cinegraph.ingestion.speaker_review.workflow import (
    SpeakerReviewRunState,
    SpeakerReviewWorkflow,
)


class ProviderMustNotBeCalled:
    def submit(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("process_primary_results must not submit")

    def retrieve(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("process_primary_results must not retrieve")

    def download_file(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("process_primary_results must not download")


def _workflow(*, maximum_authorized_cost_usd: float | None = None) -> SpeakerReviewWorkflow:
    return SpeakerReviewWorkflow(
        gateway=ProviderMustNotBeCalled(),  # type: ignore[arg-type]
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        final_review_model="gpt-5.6-sol",
        primary_reasoning_effort="low",
        adjudication_reasoning_effort="medium",
        final_review_reasoning_effort="high",
        maximum_authorized_cost_usd=maximum_authorized_cost_usd,
    )


def _candidate() -> SpeakerReviewCandidate:
    return SpeakerReviewCandidate(
        candidate_id="candidate-1",
        source_filename="aligned.srt",
        source_sha256="a" * 64,
        season_number=1,
        episode_number=1,
        cue_number=1,
        line_number=1,
        proposed_speaker="A",
        dialogue_text="Hello.",
        allowed_speakers=("A", "B"),
        evidence=(SpeakerReviewEvidence("evidence-1", "script", "A", "Hello."),),
    )


def _state(part_count: int = 1) -> SpeakerReviewRunState:
    ids = tuple(f"batch-{index}" for index in range(part_count))
    file_ids = tuple(f"file-{index}" for index in range(part_count))
    return SpeakerReviewRunState(
        schema_version=DEFAULT_SPEAKER_REVIEW_CONFIGURATION.schema_version,
        run_id="speaker-review-aaaaaaaaaaaaaaaa",
        status=SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED,
        created_at="2026-08-15T00:00:00+00:00",
        updated_at="2026-08-15T00:00:00+00:00",
        candidate_count=1,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        final_review_model="gpt-5.6-sol",
        prompt_version="speaker-review-v1",
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=1.0,
        actual_primary_cost_usd=0.0,
        actual_adjudication_cost_usd=0.0,
        primary_batch_id=ids[-1],
        primary_input_file_id=file_ids[-1],
        primary_part_count=part_count,
        primary_completed_part_count=part_count,
        primary_batch_ids=ids,
        primary_input_file_ids=file_ids,
    )


def _output(candidate_id: str, pass_id: str) -> dict[str, object]:
    return {
        "custom_id": f"{candidate_id}::{pass_id}",
        "response": {
            "status_code": 200,
            "body": {
                "model": "gpt-5.6-luna",
                "output_text": json.dumps(
                    {
                        "candidate_id": candidate_id,
                        "action": "accept",
                        "speaker": "A",
                        "confidence": 0.5,
                        "evidence_ids": ["evidence-1"],
                        "rationale": "Evidence supports the label.",
                    }
                ),
                "usage": {"input_tokens": 100, "output_tokens": 100},
            },
        },
    }


def test_process_primary_results_prepares_adjudication_without_provider(
    tmp_path: Path,
) -> None:
    candidate = _candidate()
    (tmp_path / "candidates.jsonl").write_text(
        json.dumps(candidate.to_dict()) + "\n",
        encoding="utf-8",
    )
    outputs = [
        _output(candidate.candidate_id, pass_id)
        for pass_id in DEFAULT_SPEAKER_REVIEW_CONFIGURATION.primary_pass_ids
    ]
    (tmp_path / "primary-part-0001-output.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in outputs),
        encoding="utf-8",
    )

    workflow = _workflow()
    prepared = workflow.process_primary_results(tmp_path, _state())

    assert prepared.status is SpeakerReviewRunStatus.ADJUDICATION_PREPARED
    assert prepared.adjudication_part_count == 1
    assert prepared.adjudication_batch_id is None
    assert prepared.adjudication_batch_ids == ()
    assert (tmp_path / "primary-verdicts.jsonl").exists()
    assert (tmp_path / "primary-parse-errors.json").exists()
    assert (tmp_path / "primary-decisions.jsonl").exists()
    assert (tmp_path / "adjudication-part-0001-requests.jsonl").exists()

    replay = workflow.process_primary_results(tmp_path, prepared)
    assert replay is prepared

    with pytest.raises(RuntimeError, match="reconciliation"):
        workflow.process_primary_results(
            tmp_path,
            replace(prepared, accepted_by_consensus=1),
        )


def test_process_primary_results_rejects_partial_observation_before_processing(
    tmp_path: Path,
) -> None:
    candidate = _candidate()
    (tmp_path / "candidates.jsonl").write_text(
        json.dumps(candidate.to_dict()) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "primary-part-0001-output.jsonl").write_text("{}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="reconciliation"):
        _workflow().process_primary_results(tmp_path, _state(part_count=2))

    assert not (tmp_path / "primary-verdicts.jsonl").exists()


def test_process_primary_results_rejects_unpriceable_usage_before_writes(
    tmp_path: Path,
) -> None:
    candidate = _candidate()
    (tmp_path / "candidates.jsonl").write_text(
        json.dumps(candidate.to_dict()) + "\n",
        encoding="utf-8",
    )
    output = _output(candidate.candidate_id, "primary-a")
    body = output["response"]["body"]  # type: ignore[index]
    body["usage"] = {"input_tokens": -1, "output_tokens": 1}  # type: ignore[index]
    (tmp_path / "primary-part-0001-output.jsonl").write_text(
        json.dumps(output) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="reconciliation"):
        _workflow().process_primary_results(tmp_path, _state())

    assert not (tmp_path / "primary-verdicts.jsonl").exists()


def test_process_primary_results_enforces_separate_authorized_ceiling(
    tmp_path: Path,
) -> None:
    candidate = _candidate()
    (tmp_path / "candidates.jsonl").write_text(
        json.dumps(candidate.to_dict()) + "\n",
        encoding="utf-8",
    )
    outputs = [
        _output(candidate.candidate_id, pass_id)
        for pass_id in DEFAULT_SPEAKER_REVIEW_CONFIGURATION.primary_pass_ids
    ]
    (tmp_path / "primary-part-0001-output.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in outputs),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="exceeds"):
        _workflow(maximum_authorized_cost_usd=0.000001).process_primary_results(
            tmp_path,
            replace(_state(), estimated_primary_cost_usd=0.0),
        )

    assert not (tmp_path / "primary-verdicts.jsonl").exists()


def test_process_primary_results_finalizes_consensus_without_provider(
    tmp_path: Path,
) -> None:
    run_id = "speaker-review-aaaaaaaaaaaaaaaa"
    source_filename = "Modern Family - 1x01.script-aligned.srt"
    source_text = "1\n00:00:01,000 --> 00:00:02,000\nA?: Hello.\n"
    corpus_root = tmp_path / "corpus"
    run_directory = corpus_root / "review-runs" / run_id
    run_directory.mkdir(parents=True)
    (corpus_root / source_filename).write_bytes(source_text.encode("utf-8"))
    candidate = replace(
        _candidate(),
        source_filename=source_filename,
        source_sha256=sha256(source_text.encode()).hexdigest(),
        line_number=3,
    )
    (run_directory / "candidates.jsonl").write_text(
        json.dumps(candidate.to_dict()) + "\n",
        encoding="utf-8",
    )
    outputs = []
    for pass_id in DEFAULT_SPEAKER_REVIEW_CONFIGURATION.primary_pass_ids:
        item = _output(candidate.candidate_id, pass_id)
        body = item["response"]["body"]  # type: ignore[index]
        structured = json.loads(body["output_text"])  # type: ignore[index]
        structured["action"] = "accept_candidate"
        structured["confidence"] = 0.99
        body["output_text"] = json.dumps(structured)  # type: ignore[index]
        outputs.append(item)
    (run_directory / "primary-part-0001-output.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in outputs),
        encoding="utf-8",
    )
    (run_directory / "source-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": {
                    source_filename: {
                        "path": source_filename,
                        "sha256": sha256(source_text.encode()).hexdigest(),
                        "size": len(source_text.encode()),
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    completed = _workflow().process_primary_results(run_directory, _state())

    assert completed.status is SpeakerReviewRunStatus.COMPLETED
    assert completed.accepted_by_consensus == 1
    assert (run_directory / "review-ledger.json").exists()
    assert (
        run_directory
        / "reviewed/season-01/Modern Family - 1x01.automated-reviewed.srt"
    ).exists()
    assert _workflow().process_primary_results(run_directory, completed) is completed
