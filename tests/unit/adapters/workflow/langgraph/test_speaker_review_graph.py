from dataclasses import replace
from pathlib import Path

from cinegraph.adapters.workflow.langgraph.speaker_review_graph import (
    SpeakerReviewGraphWorkflow,
)
from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review.workflow import (
    SpeakerReviewRunState,
    save_run_state,
)


def run_state(status: SpeakerReviewRunStatus) -> SpeakerReviewRunState:
    return SpeakerReviewRunState(
        schema_version=2,
        run_id="speaker-review-test",
        status=status,
        created_at="2026-08-16T00:00:00+00:00",
        updated_at="2026-08-16T00:00:00+00:00",
        candidate_count=2,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        prompt_version="speaker-review-v1",
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=1.0,
        actual_primary_cost_usd=0.0,
        actual_adjudication_cost_usd=0.0,
        primary_part_count=1,
    )


class RecordingSpeakerReviewWorkflow:
    def __init__(self, run_directory: Path) -> None:
        self.run_directory = run_directory
        self.calls: list[str] = []

    def prepare(
        self,
        *,
        corpus_root: Path,
        seasons: tuple[int, ...],
    ) -> tuple[Path, SpeakerReviewRunState]:
        self.calls.append(f"prepare:{corpus_root}:{seasons}")
        return self.run_directory, run_state(SpeakerReviewRunStatus.PREPARED)

    def submit_primary(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
    ) -> SpeakerReviewRunState:
        self.calls.append("submit_primary")
        return replace(
            state,
            status=SpeakerReviewRunStatus.PRIMARY_SUBMITTED,
            primary_batch_id="batch-1",
            primary_batch_ids=("batch-1",),
        )

    def advance(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
    ) -> SpeakerReviewRunState:
        self.calls.append("advance")
        return replace(state, status=SpeakerReviewRunStatus.COMPLETED)

    def submit_final_review(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
    ) -> SpeakerReviewRunState:
        self.calls.append("submit_final_review")
        return replace(
            state,
            status=SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED,
            final_review_model="gpt-5.6-sol",
            final_review_part_count=1,
        )

    def retry_incomplete_final_review(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
    ) -> SpeakerReviewRunState:
        self.calls.append("retry_incomplete_final_review")
        return replace(
            state,
            status=SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED,
            final_review_retry_count=1,
        )

    def reconcile_completed_costs(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
    ) -> SpeakerReviewRunState:
        self.calls.append("reconcile_completed_costs")
        return replace(state, actual_primary_cost_usd=0.25)


def test_start_graph_prepares_and_submits_new_corpus(tmp_path: Path) -> None:
    workflow = RecordingSpeakerReviewWorkflow(tmp_path / "run")
    graph = SpeakerReviewGraphWorkflow(workflow)  # type: ignore[arg-type]

    run_directory, state = graph.start(
        corpus_root=tmp_path / "corpus",
        seasons=(1, 2),
    )

    assert run_directory == tmp_path / "run"
    assert state.status is SpeakerReviewRunStatus.PRIMARY_SUBMITTED
    assert workflow.calls == [
        f"prepare:{tmp_path / 'corpus'}:(1, 2)",
        "submit_primary",
    ]


def test_advance_graph_loads_persisted_state_and_advances_once(
    tmp_path: Path,
) -> None:
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    save_run_state(
        run_directory,
        run_state(SpeakerReviewRunStatus.PRIMARY_SUBMITTED),
    )
    workflow = RecordingSpeakerReviewWorkflow(run_directory)
    graph = SpeakerReviewGraphWorkflow(workflow)  # type: ignore[arg-type]

    _, state = graph.advance(run_directory)

    assert state.status is SpeakerReviewRunStatus.COMPLETED
    assert workflow.calls == ["advance"]


def test_terminal_run_ends_without_reinvoking_review_workflow(tmp_path: Path) -> None:
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    save_run_state(
        run_directory,
        run_state(SpeakerReviewRunStatus.NEEDS_HUMAN),
    )
    workflow = RecordingSpeakerReviewWorkflow(run_directory)
    graph = SpeakerReviewGraphWorkflow(workflow)  # type: ignore[arg-type]

    _, state = graph.advance(run_directory)

    assert state.status is SpeakerReviewRunStatus.NEEDS_HUMAN
    assert workflow.calls == []


def test_final_review_graph_resumes_needs_human_run(tmp_path: Path) -> None:
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    save_run_state(
        run_directory,
        run_state(SpeakerReviewRunStatus.NEEDS_HUMAN),
    )
    workflow = RecordingSpeakerReviewWorkflow(run_directory)
    graph = SpeakerReviewGraphWorkflow(workflow)  # type: ignore[arg-type]

    _, state = graph.final_review(run_directory)

    assert state.status is SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED
    assert workflow.calls == ["submit_final_review"]


def test_retry_graph_targets_incomplete_final_verdicts(tmp_path: Path) -> None:
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    save_run_state(
        run_directory,
        run_state(SpeakerReviewRunStatus.NEEDS_HUMAN),
    )
    workflow = RecordingSpeakerReviewWorkflow(run_directory)
    graph = SpeakerReviewGraphWorkflow(workflow)  # type: ignore[arg-type]

    _, state = graph.retry_incomplete(run_directory)

    assert state.status is SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED
    assert state.final_review_retry_count == 1
    assert workflow.calls == ["retry_incomplete_final_review"]


def test_reconcile_graph_reprices_completed_raw_outputs(tmp_path: Path) -> None:
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    save_run_state(
        run_directory,
        run_state(SpeakerReviewRunStatus.NEEDS_HUMAN),
    )
    workflow = RecordingSpeakerReviewWorkflow(run_directory)
    graph = SpeakerReviewGraphWorkflow(workflow)  # type: ignore[arg-type]

    _, state = graph.reconcile_costs(run_directory)

    assert state.actual_primary_cost_usd == 0.25
    assert workflow.calls == ["reconcile_completed_costs"]
