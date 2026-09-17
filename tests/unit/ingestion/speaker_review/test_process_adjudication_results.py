from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from cinegraph.config import DEFAULT_SPEAKER_REVIEW_CONFIGURATION as CONFIG
from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review.batch_requests import (
    build_adjudication_batch_requests,
    build_primary_batch_requests,
)
from cinegraph.ingestion.speaker_review.batch_results import parse_batch_results
from cinegraph.ingestion.speaker_review.candidates import candidate_from_dict
from cinegraph.ingestion.speaker_review.costs import (
    actual_batch_output_cost_usd,
    estimate_batch_cost_usd,
)
from cinegraph.ingestion.speaker_review.decisions import decide_primary_consensus
from cinegraph.ingestion.speaker_review.workflow import (
    SpeakerReviewRunState,
    SpeakerReviewWorkflow,
    save_run_state,
)
from cinegraph.ports.llm.speaker_review_batch_gateway import BatchSubmission

RUN_ID = "speaker-review-aaaaaaaaaaaaaaaa"
PRIMARY_MODEL = "gpt-5.6-luna"
ADJUDICATION_MODEL = "gpt-5.6-terra"
FINAL_MODEL = "gpt-5.6-sol"


class ProviderMustNotBeCalled:
    def submit(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("adjudication processing must not submit")

    def retrieve(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("adjudication processing must not retrieve")

    def download_file(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("adjudication processing must not download")


def _workflow(*, maximum_authorized_cost_usd: float | None = None) -> SpeakerReviewWorkflow:
    return SpeakerReviewWorkflow(
        gateway=ProviderMustNotBeCalled(),  # type: ignore[arg-type]
        configuration=CONFIG,
        primary_model=PRIMARY_MODEL,
        adjudication_model=ADJUDICATION_MODEL,
        final_review_model=FINAL_MODEL,
        primary_reasoning_effort="low",
        adjudication_reasoning_effort="medium",
        final_review_reasoning_effort="high",
        maximum_authorized_cost_usd=maximum_authorized_cost_usd,
    )


def _candidate(candidate_id: str, line: int, source_hash: str) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "source_filename": "Modern Family - 2x01.script-aligned.srt",
        "source_sha256": source_hash,
        "episode": {"season": 2, "episode": 1},
        "cue_number": line,
        "line_number": line + 2,
        "proposed_speaker": "CLAIRE",
        "dialogue_text": f"Hello {candidate_id}.",
        "allowed_speakers": ["CLAIRE", "PHIL"],
        "evidence": [
            {
                "evidence_id": f"evidence-{candidate_id}",
                "source": "screenplay",
                "speaker": "CLAIRE",
                "text": f"Hello {candidate_id}.",
                "similarity_score": 100.0,
            }
        ],
    }


def _verdict_line(
    candidate_id: str,
    pass_id: str,
    *,
    action: str,
    confidence: float,
    model: str,
    input_tokens: int = 100,
    output_tokens: int = 20,
) -> dict[str, object]:
    verdict = {
        "candidate_id": candidate_id,
        "action": action,
        "speaker": "CLAIRE",
        "confidence": confidence,
        "evidence_ids": [f"evidence-{candidate_id}"],
        "rationale": "The bounded evidence supports the label.",
    }
    return {
        "custom_id": f"{candidate_id}::{pass_id}",
        "response": {
            "status_code": 200,
            "body": {
                "id": f"response-{candidate_id}-{pass_id}",
                "model": model,
                "output_text": json.dumps(verdict, separators=(",", ":")),
                "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
            },
        },
    }


def _source_manifest(source: bytes) -> dict[str, object]:
    return {
        "schema_version": 1,
        "sources": {
            "Modern Family - 2x01.script-aligned.srt": {
                "path": "Modern Family - 2x01.script-aligned.srt",
                "sha256": sha256(source).hexdigest(),
                "size": len(source),
            }
        },
    }


def _binding(run_id: str, stage: str, part: int, request: bytes) -> dict[str, object]:
    return {
        "schema_version": 1,
        "request_sha256": sha256(request).hexdigest(),
        "run_id": run_id,
        "stage": stage,
        "part": part,
        "prompt_version": CONFIG.prompt_version,
        "batch_endpoint": CONFIG.batch_endpoint,
        "completion_window": CONFIG.batch_completion_window,
    }


def _write_json(path: Path, value: object) -> None:
    path.write_bytes((json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode())


def _write_stage(
    run: Path,
    stage: str,
    part: int,
    requests: list[dict[str, object]],
    outputs: list[dict[str, object]],
    *,
    batch_id: str,
    input_id: str,
) -> None:
    request = b"".join(
        (json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n").encode()
        for item in requests
    )
    (run / f"{stage}-part-{part:04d}-requests.jsonl").write_bytes(request)
    binding = _binding(RUN_ID, stage, part, request)
    _write_json(
        run / f".{stage}-part-{part:04d}-submission-intent.json",
        {"binding": binding, "status": "intent"},
    )
    _write_json(
        run / f".{stage}-part-{part:04d}-submission-completed.json",
        {"binding": binding, "batch_id": batch_id, "input_file_id": input_id, "status": "completed"},
    )
    output = b"".join((json.dumps(item, separators=(",", ":")) + "\n").encode() for item in outputs)
    (run / f"{stage}-part-{part:04d}-output.jsonl").write_bytes(output)


def _state(
    *,
    status: SpeakerReviewRunStatus = SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED,
    candidate_count: int = 2,
    primary_parts: int = 1,
    adjudication_parts: int = 1,
    actual_primary: float = 0.0,
    accepted_by_consensus: int = 1,
    accepted_by_adjudication: int = 0,
    needs_human: int = 0,
    actual_adjudication: float = 0.0,
    final_parts: int = 0,
    estimated_primary: float = 0.1,
) -> SpeakerReviewRunState:
    primary_ids = tuple(f"primary-batch-{i}" for i in range(primary_parts))
    primary_inputs = tuple(f"primary-input-{i}" for i in range(primary_parts))
    adjudication_ids = tuple(f"adjudication-batch-{i}" for i in range(adjudication_parts))
    adjudication_inputs = tuple(f"adjudication-input-{i}" for i in range(adjudication_parts))
    return SpeakerReviewRunState(
        schema_version=CONFIG.schema_version,
        run_id=RUN_ID,
        status=status,
        created_at="2026-08-15T00:00:00+00:00",
        updated_at="2026-08-15T00:00:00+00:00",
        candidate_count=candidate_count,
        primary_model=PRIMARY_MODEL,
        adjudication_model=ADJUDICATION_MODEL,
        final_review_model=FINAL_MODEL,
        prompt_version=CONFIG.prompt_version,
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=estimated_primary,
        actual_primary_cost_usd=actual_primary,
        actual_adjudication_cost_usd=actual_adjudication,
        primary_batch_id=primary_ids[-1],
        primary_input_file_id=primary_inputs[-1],
        primary_part_count=primary_parts,
        primary_completed_part_count=primary_parts,
        primary_batch_ids=primary_ids,
        primary_input_file_ids=primary_inputs,
        adjudication_batch_id=adjudication_ids[-1],
        adjudication_input_file_id=adjudication_inputs[-1],
        adjudication_part_count=adjudication_parts,
        adjudication_completed_part_count=adjudication_parts,
        adjudication_batch_ids=adjudication_ids,
        adjudication_input_file_ids=adjudication_inputs,
        final_review_part_count=final_parts,
        accepted_by_consensus=accepted_by_consensus,
        accepted_by_adjudication=accepted_by_adjudication,
        needs_human=needs_human,
    )


def _fixture(
    tmp_path: Path,
    *,
    adjudication_action: str = "needs_review",
    adjudication_confidence: float = 0.1,
    status: SpeakerReviewRunStatus = SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED,
    primary_parts: int = 1,
    adjudication_parts: int = 1,
    usage_tokens: tuple[int, int] = (100, 20),
) -> tuple[Path, SpeakerReviewRunState]:
    corpus = tmp_path / "corpus"
    run = corpus / "review-runs" / RUN_ID
    run.mkdir(parents=True)
    source = (
        b"1\n00:00:01,000 --> 00:00:02,000\nA?: Hello candidate-1.\n\n"
        b"2\n00:00:03,000 --> 00:00:04,000\nA?: Hello candidate-2.\n"
    )
    (corpus / "Modern Family - 2x01.script-aligned.srt").write_bytes(source)
    source_hash = sha256(source).hexdigest()
    candidates = [_candidate("candidate-1", 1, source_hash), _candidate("candidate-2", 5, source_hash)]
    (run / "candidates.jsonl").write_text("".join(json.dumps(item) + "\n" for item in candidates), encoding="utf-8")
    _write_json(run / "source-manifest.json", _source_manifest(source))
    candidate_models = tuple(candidate_from_dict(item) for item in candidates)

    primary_outputs = [
        _verdict_line("candidate-1", pass_id, action="accept_candidate", confidence=0.99, model=PRIMARY_MODEL, input_tokens=usage_tokens[0], output_tokens=usage_tokens[1])
        for pass_id in CONFIG.primary_pass_ids
    ] + [
        _verdict_line("candidate-2", pass_id, action="needs_review", confidence=0.1, model=PRIMARY_MODEL, input_tokens=usage_tokens[0], output_tokens=usage_tokens[1])
        for pass_id in CONFIG.primary_pass_ids
    ]
    primary_verdicts, primary_errors = parse_batch_results(
        output_jsonl="".join(json.dumps(item) + "\n" for item in primary_outputs),
        candidates={item.candidate_id: item for item in candidate_models},
        configuration=CONFIG,
    )
    primary_decisions = decide_primary_consensus(
        candidates=candidate_models, verdicts=primary_verdicts, configuration=CONFIG
    )
    primary_requests = list(
        build_primary_batch_requests(
            candidates=candidate_models,
            model=PRIMARY_MODEL,
            reasoning_effort="low",
            configuration=CONFIG,
        )
    )
    adjudication_candidates = tuple(
        candidate
        for candidate, decision in zip(candidate_models, primary_decisions)
        if decision.disposition.value == "adjudication_required"
    )
    adjudication_requests = list(
        build_adjudication_batch_requests(
            candidates=adjudication_candidates,
            primary_verdicts=primary_verdicts,
            model=ADJUDICATION_MODEL,
            reasoning_effort="medium",
            configuration=CONFIG,
        )
    )
    adjudication_outputs = [
        _verdict_line(
            "candidate-2",
            CONFIG.adjudication_pass_id,
            action=adjudication_action,
            confidence=adjudication_confidence,
            model=ADJUDICATION_MODEL,
            input_tokens=usage_tokens[0],
            output_tokens=usage_tokens[1],
        )
    ]
    primary_cost = actual_batch_output_cost_usd(
        output_jsonl="".join(json.dumps(item) + "\n" for item in primary_outputs),
        configured_model=PRIMARY_MODEL,
        configuration=CONFIG,
    )
    actual_batch_output_cost_usd(
        output_jsonl="".join(json.dumps(item) + "\n" for item in adjudication_outputs),
        configured_model=ADJUDICATION_MODEL,
        configuration=CONFIG,
    )
    state = _state(
        status=status,
        actual_primary=primary_cost,
        accepted_by_adjudication=0,
        needs_human=0,
        actual_adjudication=0.0,
        final_parts=0,
        estimated_primary=estimate_batch_cost_usd(
            requests=tuple(primary_requests), model=PRIMARY_MODEL, configuration=CONFIG
        ),
        primary_parts=primary_parts,
        adjudication_parts=adjudication_parts,
    )
    # Repeat the same complete evidence in each part for a generic multi-part shape test.
    for part in range(1, primary_parts + 1):
        _write_stage(run, "primary", part, primary_requests, primary_outputs, batch_id=f"primary-batch-{part - 1}", input_id=f"primary-input-{part - 1}")
    for part in range(1, adjudication_parts + 1):
        _write_stage(run, "adjudication", part, adjudication_requests, adjudication_outputs, batch_id=f"adjudication-batch-{part - 1}", input_id=f"adjudication-input-{part - 1}")
    (run / "primary-verdicts.jsonl").write_bytes(
        "".join(
            json.dumps(verdict.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
            for candidate_id in sorted(primary_verdicts)
            for verdict in primary_verdicts[candidate_id]
        ).encode()
    )
    _write_json(run / "primary-parse-errors.json", list(primary_errors))
    (run / "primary-decisions.jsonl").write_bytes(
        "".join(json.dumps(item.to_dict(), ensure_ascii=False, sort_keys=True) + "\n" for item in primary_decisions).encode()
    )
    save_run_state(run, state)
    return run, state


def test_fresh_unresolved_prepares_exact_final_requests_without_provider(tmp_path: Path) -> None:
    run, state = _fixture(tmp_path)
    before = {path.name: path.read_bytes() for path in run.iterdir()}

    prepared = _workflow().process_adjudication_results(run, state)

    assert prepared.status is SpeakerReviewRunStatus.FINAL_REVIEW_PREPARED
    assert prepared.final_review_part_count == 1
    assert prepared.final_review_batch_ids == ()
    assert prepared.final_review_input_file_ids == ()
    assert prepared.needs_human == 1
    assert (run / "final-review-part-0001-requests.jsonl").read_bytes()
    assert {path.name: path.read_bytes() for path in run.iterdir() if path.name in before and path.name != "run-state.json"} == {
        name: raw for name, raw in before.items() if name != "run-state.json"
    }

    replay = _workflow().process_adjudication_results(run, prepared)
    assert replay is prepared


def test_fully_resolved_finalizes_exact_reviewed_outputs(tmp_path: Path) -> None:
    run, state = _fixture(tmp_path, adjudication_action="accept_candidate", adjudication_confidence=0.99)

    completed = _workflow().process_adjudication_results(run, state)

    assert completed.status is SpeakerReviewRunStatus.COMPLETED
    assert completed.accepted_by_consensus == 1
    assert completed.accepted_by_adjudication == 1
    assert completed.needs_human == 0
    assert not list(run.glob("final-review-part-*-requests.jsonl"))
    reviewed = run / "reviewed/season-02/Modern Family - 2x01.automated-reviewed.srt"
    assert reviewed.exists()
    assert "CLAIRE: Hello candidate-1." in reviewed.read_text(encoding="utf-8")
    assert "CLAIRE: Hello candidate-2." in reviewed.read_text(encoding="utf-8")
    assert (run / "review-ledger.json").exists()
    assert (run / "calibration-sample.json").exists()
    assert _workflow().process_adjudication_results(run, completed) == completed


@pytest.mark.parametrize("field", ["candidate_count", "primary_part_count", "adjudication_part_count"])
def test_incomplete_or_extra_part_shape_is_rejected_before_writes(tmp_path: Path, field: str) -> None:
    run, state = _fixture(tmp_path)
    bad = replace(state, **{field: getattr(state, field) + 1})
    with pytest.raises(RuntimeError, match="reconciliation"):
        _workflow().process_adjudication_results(run, bad)
    assert not (run / "final-review-part-0001-requests.jsonl").exists()


@pytest.mark.parametrize(
    "mutator",
    [
        lambda run: (run / "adjudication-part-0001-requests.jsonl").write_bytes(b"{}\n"),
        lambda run: (run / "adjudication-part-0001-output.jsonl").write_text("{}\n", encoding="utf-8"),
        lambda run: (run / ".adjudication-part-0001-submission-completed.json").write_text("{}\n", encoding="utf-8"),
    ],
)
def test_request_journal_output_and_extra_evidence_reconcile(tmp_path: Path, mutator) -> None:
    run, state = _fixture(tmp_path)
    mutator(run)
    with pytest.raises(RuntimeError, match="reconciliation"):
        _workflow().process_adjudication_results(run, state)


def test_output_and_api_error_custom_id_union_must_match_requests(tmp_path: Path) -> None:
    run, state = _fixture(tmp_path)
    error = run / "adjudication-part-0001-api-errors.jsonl"
    error.write_text(json.dumps({"custom_id": "candidate-2::adjudication"}) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="reconciliation"):
        _workflow().process_adjudication_results(run, state)


def test_custom_id_reconciliation_rejects_distinct_two_part_swap(tmp_path: Path) -> None:
    (tmp_path / "primary-part-0001-requests.jsonl").write_bytes(b'{"custom_id":"candidate-a::primary-a"}\n')
    (tmp_path / "primary-part-0002-requests.jsonl").write_bytes(b'{"custom_id":"candidate-b::primary-a"}\n')
    (tmp_path / "primary-part-0001-output.jsonl").write_bytes(b'{"custom_id":"candidate-b::primary-a"}\n')
    (tmp_path / "primary-part-0002-output.jsonl").write_bytes(b'{"custom_id":"candidate-a::primary-a"}\n')

    with pytest.raises(ValueError):
        SpeakerReviewWorkflow._validate_output_custom_ids(  # type: ignore[arg-type]
            tmp_path,
            stage="primary",
            part_count=2,
        )


def test_tampered_request_body_is_rejected_even_when_custom_ids_and_journal_bindings_match(
    tmp_path: Path,
) -> None:
    run, state = _fixture(tmp_path)
    request_path = run / "primary-part-0001-requests.jsonl"
    rows = [json.loads(line) for line in request_path.read_text(encoding="utf-8").splitlines()]
    rows[0]["body"]["max_output_tokens"] = 1  # type: ignore[index]
    request = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows).encode()
    request_path.write_bytes(request)
    request_hash = sha256(request).hexdigest()
    for kind in ("intent", "completed"):
        journal = run / f".primary-part-0001-submission-{kind}.json"
        value = json.loads(journal.read_text(encoding="utf-8"))
        value["binding"]["request_sha256"] = request_hash  # type: ignore[index]
        _write_json(journal, value)

    with pytest.raises(RuntimeError, match="reconciliation"):
        _workflow().process_adjudication_results(run, state)


@pytest.mark.parametrize("usage", [{"input_tokens": -1, "output_tokens": 1}, {"input_tokens": 1}, {"input_tokens": 2_000_001, "output_tokens": 1}, {"input_tokens": 20_000_000, "output_tokens": 1}])
def test_malformed_usage_and_each_recording_cap_reconcile(tmp_path: Path, usage: dict[str, int]) -> None:
    run, state = _fixture(tmp_path)
    payload = json.loads((run / "adjudication-part-0001-output.jsonl").read_text(encoding="utf-8"))
    payload["response"]["body"]["usage"] = usage  # type: ignore[index]
    (run / "adjudication-part-0001-output.jsonl").write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="reconciliation"):
        _workflow().process_adjudication_results(run, state)


@pytest.mark.parametrize("field", ["actual_primary_cost_usd", "actual_adjudication_cost_usd"])
def test_cost_checkpoint_mismatch_reconciles(tmp_path: Path, field: str) -> None:
    run, state = _fixture(tmp_path)
    bad = replace(state, **{field: 0.0 if getattr(state, field) else 1.0})
    with pytest.raises(RuntimeError, match="reconciliation"):
        _workflow().process_adjudication_results(run, bad)


def test_tampered_estimated_primary_cost_is_rejected_before_new_artifacts(
    tmp_path: Path,
) -> None:
    run, state = _fixture(tmp_path)
    bad = replace(state, estimated_primary_cost_usd=state.estimated_primary_cost_usd + 0.01)
    with pytest.raises(RuntimeError, match="reconciliation"):
        _workflow().process_adjudication_results(run, bad)
    assert not (run / "final-review-part-0001-requests.jsonl").exists()


def test_zero_cost_outputs_are_accepted_and_replayed(tmp_path: Path) -> None:
    run, state = _fixture(tmp_path, usage_tokens=(0, 0))
    prepared = _workflow().process_adjudication_results(run, state)
    assert prepared.actual_adjudication_cost_usd == 0.0
    assert _workflow().process_adjudication_results(run, prepared) == prepared


def test_partial_derived_artifacts_are_recovered_deterministically(tmp_path: Path) -> None:
    run, state = _fixture(tmp_path)
    prepared = _workflow().process_adjudication_results(run, state)
    expected = {
        name: (run / name).read_bytes()
        for name in (
            "adjudication-verdicts.jsonl",
            "adjudication-parse-errors.json",
            "final-decisions.jsonl",
        )
    }
    for name in expected:
        (run / name).unlink()
    recovered = _workflow().process_adjudication_results(run, state)
    assert recovered.status is SpeakerReviewRunStatus.FINAL_REVIEW_PREPARED
    assert {name: (run / name).read_bytes() for name in expected} == expected
    assert _workflow().process_adjudication_results(run, prepared).status is SpeakerReviewRunStatus.FINAL_REVIEW_PREPARED


def test_submit_final_review_validates_prepared_request_before_one_provider_submit(tmp_path: Path) -> None:
    run, state = _fixture(tmp_path)
    prepared = _workflow().process_adjudication_results(run, state)
    calls: list[bytes] = []

    class Gateway:
        def submit(self, name: str, request: bytes, completion_window: str, metadata: dict[str, str]) -> BatchSubmission:
            del name, completion_window, metadata
            calls.append(request)
            return BatchSubmission("final-batch-1", "final-input-1", "submitted")

        def retrieve(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("submit_final_review must not retrieve")

        def download_file(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("submit_final_review must not download")

    submitting = SpeakerReviewWorkflow(
        gateway=Gateway(),  # type: ignore[arg-type]
        configuration=CONFIG,
        primary_model=PRIMARY_MODEL,
        adjudication_model=ADJUDICATION_MODEL,
        final_review_model=FINAL_MODEL,
        primary_reasoning_effort="low",
        adjudication_reasoning_effort="medium",
        final_review_reasoning_effort="high",
    )
    submitted = submitting.submit_final_review(run, prepared)
    assert submitted.status is SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED
    assert len(calls) == 1
    request = calls[0]
    assert request == (run / "final-review-part-0001-requests.jsonl").read_bytes()

    tampered = bytearray(request)
    tampered[-2:-1] = b"x"
    (run / "final-review-part-0001-requests.jsonl").write_bytes(tampered)
    with pytest.raises(RuntimeError, match="reconciliation"):
        submitting.submit_final_review(run, prepared)
    assert len(calls) == 1
