from dataclasses import replace
from pathlib import Path

from cinegraph.adapters.workflow.langgraph.speaker_review_graph import (
    SpeakerReviewGraphWorkflow,
)
from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review.workflow import (
    SpeakerReviewRunState,
    load_run_state,
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

    def load(
        self,
        run_directory: Path,
    ) -> tuple[Path, SpeakerReviewRunState]:
        self.calls.append("load")
        return run_directory, load_run_state(run_directory / "run-state.json")

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

    def observe_primary_part(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
    ) -> SpeakerReviewRunState:
        self.calls.append("observe_primary_part")
        return replace(
            state,
            status=SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED,
            primary_completed_part_count=state.primary_completed_part_count + 1,
        )

    def submit_next_primary_part(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
    ) -> SpeakerReviewRunState:
        self.calls.append("submit_next_primary_part")
        return replace(state, status=SpeakerReviewRunStatus.PRIMARY_SUBMITTED)

    def submit_next_adjudication_part(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
    ) -> SpeakerReviewRunState:
        self.calls.append("submit_next_adjudication_part")
        return replace(state, status=SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED)

    def observe_next_adjudication(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
    ) -> SpeakerReviewRunState:
        self.calls.append("observe_next_adjudication")
        return replace(
            state,
            status=SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED,
            adjudication_completed_part_count=state.adjudication_completed_part_count + 1,
        )

    def observe_final_review_part_one(
        self, run_directory: Path, state: SpeakerReviewRunState
    ) -> SpeakerReviewRunState:
        self.calls.append("observe_final_review_part_one")
        return replace(state, final_review_completed_part_count=1)

    def process_primary_results(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
    ) -> SpeakerReviewRunState:
        self.calls.append("process_primary_results")
        return replace(state, status=SpeakerReviewRunStatus.ADJUDICATION_PREPARED)

    def process_adjudication_results(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
    ) -> SpeakerReviewRunState:
        self.calls.append("process_adjudication_results")
        return replace(state, status=SpeakerReviewRunStatus.FINAL_REVIEW_PREPARED)

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


def test_prepare_graph_stops_before_provider_submission(tmp_path: Path) -> None:
    workflow = RecordingSpeakerReviewWorkflow(tmp_path / "run")
    graph = SpeakerReviewGraphWorkflow(workflow)  # type: ignore[arg-type]

    run_directory, state = graph.prepare(
        corpus_root=tmp_path / "corpus",
        seasons=(2,),
    )

    assert run_directory == tmp_path / "run"
    assert state.status is SpeakerReviewRunStatus.PREPARED
    assert workflow.calls == [f"prepare:{tmp_path / 'corpus'}:(2,)"]


def test_submit_graph_loads_and_invokes_only_primary_submission(tmp_path: Path) -> None:
    run_directory = tmp_path / "review-workspace" / "review-runs" / "speaker-review-test"
    run_directory.mkdir(parents=True)
    save_run_state(run_directory, run_state(SpeakerReviewRunStatus.PREPARED))
    workflow = RecordingSpeakerReviewWorkflow(run_directory)
    graph = SpeakerReviewGraphWorkflow(workflow)  # type: ignore[arg-type]

    _, state = graph.submit(run_directory)

    assert state.status is SpeakerReviewRunStatus.PRIMARY_SUBMITTED
    assert workflow.calls == ["load", "submit_primary"]


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
    assert workflow.calls == ["load", "advance"]


def test_observe_primary_graph_loads_and_observes_without_advancing(
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

    _, state = graph.observe_primary(run_directory)

    assert state.status is SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED
    assert workflow.calls == ["load", "observe_primary_part"]


def test_observe_primary_graph_stops_for_completed_part_without_workflow_call(
    tmp_path: Path,
) -> None:
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    save_run_state(
        run_directory,
        run_state(SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED),
    )
    workflow = RecordingSpeakerReviewWorkflow(run_directory)
    graph = SpeakerReviewGraphWorkflow(workflow)  # type: ignore[arg-type]

    _, state = graph.observe_primary(run_directory)

    assert state.status is SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED
    assert workflow.calls == ["load"]


def test_observe_primary_graph_uses_root_verified_checkpoint_without_reload(
    tmp_path: Path,
) -> None:
    run_directory = tmp_path / "run"
    checkpoint = replace(
        run_state(SpeakerReviewRunStatus.PRIMARY_SUBMITTED),
        primary_part_count=2,
        primary_completed_part_count=1,
        primary_batch_id="batch-2",
        primary_input_file_id="file-2",
        primary_batch_ids=("batch-1", "batch-2"),
        primary_input_file_ids=("file-1", "file-2"),
    )
    workflow = RecordingSpeakerReviewWorkflow(run_directory)
    graph = SpeakerReviewGraphWorkflow(workflow)  # type: ignore[arg-type]

    _, state = graph.observe_primary(
        run_directory,
        verified_run_state=checkpoint,
    )

    assert state.status is SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED
    assert workflow.calls == ["observe_primary_part"]


def test_submit_next_primary_graph_loads_checkpoint_and_stops_after_one_node(
    tmp_path: Path,
) -> None:
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    checkpoint = replace(
        run_state(SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED),
        primary_completed_part_count=1,
        primary_batch_id="batch-1",
        primary_input_file_id="file-1",
        primary_batch_ids=("batch-1",),
        primary_input_file_ids=("file-1",),
    )
    save_run_state(run_directory, checkpoint)
    workflow = RecordingSpeakerReviewWorkflow(run_directory)
    graph = SpeakerReviewGraphWorkflow(workflow)  # type: ignore[arg-type]

    _, state = graph.submit_next_primary(run_directory)

    assert state.status is SpeakerReviewRunStatus.PRIMARY_SUBMITTED
    assert workflow.calls == ["load", "submit_next_primary_part"]


def test_submit_next_primary_graph_uses_root_verified_checkpoint_without_reload(
    tmp_path: Path,
) -> None:
    run_directory = tmp_path / "run"
    checkpoint = replace(
        run_state(SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED),
        primary_part_count=2,
        primary_completed_part_count=1,
        primary_batch_id="batch-1",
        primary_input_file_id="file-1",
        primary_batch_ids=("batch-1",),
        primary_input_file_ids=("file-1",),
    )
    workflow = RecordingSpeakerReviewWorkflow(run_directory)
    graph = SpeakerReviewGraphWorkflow(workflow)  # type: ignore[arg-type]

    _, state = graph.submit_next_primary(
        run_directory,
        verified_run_state=checkpoint,
    )

    assert state.status is SpeakerReviewRunStatus.PRIMARY_SUBMITTED
    assert workflow.calls == ["submit_next_primary_part"]


def test_submit_next_adjudication_graph_loads_and_invokes_only_submission(
    tmp_path: Path,
) -> None:
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    checkpoint = replace(
        run_state(SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED),
        adjudication_part_count=3,
        adjudication_completed_part_count=1,
        adjudication_batch_id="batch-1",
        adjudication_input_file_id="file-1",
        adjudication_batch_ids=("batch-1",),
        adjudication_input_file_ids=("file-1",),
    )
    save_run_state(run_directory, checkpoint)
    workflow = RecordingSpeakerReviewWorkflow(run_directory)
    graph = SpeakerReviewGraphWorkflow(workflow)  # type: ignore[arg-type]

    _, state = graph.submit_next_adjudication(run_directory)

    assert state.status is SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED
    assert workflow.calls == ["load", "submit_next_adjudication_part"]


def test_submit_next_adjudication_uses_verified_state_without_reload(
    tmp_path: Path,
) -> None:
    run_directory = tmp_path / "run"
    checkpoint = replace(
        run_state(SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED),
        adjudication_part_count=3,
        adjudication_completed_part_count=1,
        adjudication_batch_id="batch-1",
        adjudication_input_file_id="file-1",
        adjudication_batch_ids=("batch-1",),
        adjudication_input_file_ids=("file-1",),
    )
    workflow = RecordingSpeakerReviewWorkflow(run_directory)
    graph = SpeakerReviewGraphWorkflow(workflow)  # type: ignore[arg-type]

    _, state = graph.submit_next_adjudication(
        run_directory,
        verified_run_state=checkpoint,
    )

    assert state.status is SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED
    assert workflow.calls == ["submit_next_adjudication_part"]


def test_observe_next_adjudication_graph_routes_part_two_and_stops() -> None:
    run_directory = Path("run")
    checkpoint = replace(
        run_state(SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED),
        adjudication_part_count=3,
        adjudication_completed_part_count=1,
        adjudication_batch_id="batch-2",
        adjudication_input_file_id="file-2",
        adjudication_batch_ids=("batch-1", "batch-2"),
        adjudication_input_file_ids=("file-1", "file-2"),
    )
    workflow = RecordingSpeakerReviewWorkflow(run_directory)
    graph = SpeakerReviewGraphWorkflow(workflow)  # type: ignore[arg-type]

    _, state = graph.observe_next_adjudication(
        run_directory,
        verified_run_state=checkpoint,
    )

    assert state.status is SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED
    assert state.adjudication_completed_part_count == 2
    assert workflow.calls == ["observe_next_adjudication"]


def test_observe_final_review_part_one_graph_routes_exact_operation() -> None:
    run_directory = Path("run")
    checkpoint = replace(
        run_state(SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED),
        final_review_part_count=2,
        final_review_completed_part_count=0,
        final_review_batch_id="final-1",
        final_review_input_file_id="input-1",
        final_review_batch_ids=("final-1",),
        final_review_input_file_ids=("input-1",),
    )
    workflow = RecordingSpeakerReviewWorkflow(run_directory)
    graph = SpeakerReviewGraphWorkflow(workflow)  # type: ignore[arg-type]

    _, state = graph.observe_final_review_part_one(
        run_directory,
        verified_run_state=checkpoint,
    )

    assert state.status is SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED
    assert state.final_review_completed_part_count == 1
    assert workflow.calls == ["observe_final_review_part_one"]


def test_process_primary_results_graph_uses_verified_checkpoint_without_reload(
    tmp_path: Path,
) -> None:
    run_directory = tmp_path / "run"
    checkpoint = replace(
        run_state(SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED),
        primary_completed_part_count=1,
        primary_batch_id="batch-1",
        primary_input_file_id="file-1",
        primary_batch_ids=("batch-1",),
        primary_input_file_ids=("file-1",),
    )
    workflow = RecordingSpeakerReviewWorkflow(run_directory)
    graph = SpeakerReviewGraphWorkflow(workflow)  # type: ignore[arg-type]

    _, state = graph.process_primary_results(
        run_directory,
        verified_run_state=checkpoint,
    )

    assert state.status is SpeakerReviewRunStatus.ADJUDICATION_PREPARED
    assert workflow.calls == ["process_primary_results"]


def test_process_adjudication_results_graph_uses_verified_checkpoint_without_reload(
    tmp_path: Path,
) -> None:
    run_directory = tmp_path / "run"
    checkpoint = replace(
        run_state(SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED),
        primary_completed_part_count=1,
        primary_batch_id="batch-1",
        primary_input_file_id="file-1",
        primary_batch_ids=("batch-1",),
        primary_input_file_ids=("file-1",),
        adjudication_part_count=1,
        adjudication_completed_part_count=1,
        adjudication_batch_id="adjudication-batch-1",
        adjudication_input_file_id="adjudication-file-1",
        adjudication_batch_ids=("adjudication-batch-1",),
        adjudication_input_file_ids=("adjudication-file-1",),
    )
    workflow = RecordingSpeakerReviewWorkflow(run_directory)
    graph = SpeakerReviewGraphWorkflow(workflow)  # type: ignore[arg-type]

    _, state = graph.process_adjudication_results(
        run_directory,
        verified_run_state=checkpoint,
    )

    assert state.status is SpeakerReviewRunStatus.FINAL_REVIEW_PREPARED
    assert workflow.calls == ["process_adjudication_results"]


def test_submit_next_primary_graph_ignores_unrelated_terminal_state(
    tmp_path: Path,
) -> None:
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    save_run_state(run_directory, run_state(SpeakerReviewRunStatus.COMPLETED))
    workflow = RecordingSpeakerReviewWorkflow(run_directory)
    graph = SpeakerReviewGraphWorkflow(workflow)  # type: ignore[arg-type]

    _, state = graph.submit_next_primary(run_directory)

    assert state.status is SpeakerReviewRunStatus.COMPLETED
    assert workflow.calls == ["load"]


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
    assert workflow.calls == ["load"]


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
    assert workflow.calls == ["load", "submit_final_review"]


def test_final_review_graph_direct_routes_verified_state(tmp_path: Path) -> None:
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    state = run_state(SpeakerReviewRunStatus.FINAL_REVIEW_PREPARED)
    save_run_state(run_directory, state)
    workflow = RecordingSpeakerReviewWorkflow(run_directory)
    graph = SpeakerReviewGraphWorkflow(workflow)  # type: ignore[arg-type]

    _, updated = graph.final_review(run_directory, verified_run_state=state)

    assert updated.status is SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED
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
    assert workflow.calls == ["load", "retry_incomplete_final_review"]


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
    assert workflow.calls == ["load", "reconcile_completed_costs"]
